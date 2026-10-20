"""Offline Kite Connect integration tests. No API key or network needed."""
from __future__ import annotations
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from kite_feed import KiteFeed, cash_payload, select_instruments
from server import Scanner

IST = ZoneInfo('Asia/Kolkata')


class FakeKite:
    instance = None
    def __init__(self, api_key):
        self.api_key = api_key
        self.access_token = None
        self.history_calls = []
        self.quote_calls = []
        FakeKite.instance = self

    def set_access_token(self, token):
        self.access_token = token

    def instruments(self, exchange):
        if exchange == 'NSE':
            return [dict(instrument_token=111, tradingsymbol='PGEL', instrument_type='EQ', exchange='NSE'),
                    dict(instrument_token=222, tradingsymbol='RELIANCE', instrument_type='EQ', exchange='NSE'),
                    dict(instrument_token=333, tradingsymbol='PGEL', instrument_type='FUT', exchange='NSE')]
        today = datetime.now(IST).date()
        return [dict(instrument_token=444, name='RELIANCE', tradingsymbol='RELIANCE26OCTFUT',
                     instrument_type='FUT', segment='NFO-FUT', expiry=today + timedelta(days=14)),
                dict(instrument_token=445, name='RELIANCE', tradingsymbol='RELIANCE26NOVFUT',
                     instrument_type='FUT', segment='NFO-FUT', expiry=today + timedelta(days=44)),
                dict(instrument_token=446, name='PGEL', tradingsymbol='PGEL26OCTFUT',
                     instrument_type='FUT', segment='NFO-FUT', expiry=today - timedelta(days=1))]

    def quote(self, instruments):
        self.quote_calls.append(instruments)
        result = {}
        for sym in instruments:
            if sym.startswith('NSE:'):
                result[sym] = dict(last_price=500 if 'PGEL' in sym else 1200,
                                   ohlc=dict(close=490 if 'PGEL' in sym else 1250, open=485),
                                   volume=250000,
                                   timestamp=datetime.now(IST) - timedelta(days=1))
            elif sym.startswith('NFO:'):
                result[sym] = dict(oi=1000000)
        return result

    def historical_data(self, token, from_date, to_date, interval, oi=False):
        self.history_calls.append((token,interval))
        return [dict(date=datetime.now(IST).replace(hour=9,minute=15,second=0,microsecond=0),open=480,high=485,low=478,close=484,volume=1500),
                dict(date=datetime.now(IST).replace(hour=9,minute=20,second=0,microsecond=0),open=484,high=491,low=482,close=488,volume=2000)]


class FakeTicker:
    MODE_QUOTE = 'quote'
    MODE_FULL = 'full'
    instance = None
    def __init__(self, key, token):
        FakeTicker.instance = self
        self.key, self.token = key,token
        self.subscribed = []
        self.mode_calls = []
        self.closed = False

    def subscribe(self, tokens):
        self.subscribed = tokens

    def set_mode(self, mode, tokens):
        self.mode_calls.append((mode, tokens))

    def connect(self, threaded=False):
        assert threaded
        self.on_connect(self, None)

    def close(self):
        self.closed = True


class KiteTests(unittest.TestCase):
    def test_symbol_matching_uses_nse_cash_only_nearest_unexpired_future(self):
        kite = FakeKite('test')
        cash, fut = select_instruments(kite.instruments('NSE'),kite.instruments('NFO'),
                                       ['PGEL','RELIANCE','MISSING'],today=datetime.now(IST).date())
        self.assertEqual(cash,{'PGEL':111, 'RELIANCE':222})
        self.assertEqual(fut,{'RELIANCE':444})

    def test_quote_parsing_preserves_exchange_timestamp(self):
        payload = cash_payload('PGEL', dict(last_price=527.65,ohlc={'open':500,'close':490},
                                             volume_traded=12345, average_traded_price=511,
                                             exchange_timestamp=datetime(2026,10,20,10,7)))
        self.assertEqual(payload['timestamp'], '2026-10-20T10:07:00+05:30')
        self.assertEqual(payload['volume'],12345)
        self.assertEqual(payload['prev_close'],490)

    def test_bootstrap_stream_oi_and_history(self):
        scanner=Scanner(demo=False)
        feed=KiteFeed(scanner,'demo-key','demo-token',kite_cls=FakeKite,ticker_cls=FakeTicker)
        feed.bootstrap()
        self.assertEqual(scanner.feed_status,'connecting')
        self.assertEqual(scanner.mapped_futures,1)
        self.assertEqual(scanner.records['PGEL']['oi'],None)  # No eligible PGEL NFO future.
        self.assertEqual(scanner.records['RELIANCE']['oi'],1000000)
        self.assertEqual(scanner.records['RELIANCE']['oiChange'],0)
        feed.start_websocket()
        self.assertEqual(scanner.feed_status,'connected')
        self.assertEqual(sorted(FakeTicker.instance.subscribed),[111,222,444])
        self.assertIn(('quote',[111,222]),FakeTicker.instance.mode_calls)
        self.assertIn(('full',[444]),FakeTicker.instance.mode_calls)
        FakeTicker.instance.on_ticks(FakeTicker.instance,[
            dict(instrument_token=111,last_price=502,ohlc={'close':490,'open':485},volume_traded=250120),
            dict(instrument_token=444,oi=1020000)])
        self.assertEqual(scanner.records['PGEL']['price'],502)
        self.assertEqual(scanner.records['RELIANCE']['oiChange'],2)
        self.assertEqual(scanner.records['PGEL']['oiChange'],None)
        # During a simulation local current day may differ from mocked date, but historical
        # bars still populate and become available in the 5m chart.
        feed.hydrate_history('PGEL')
        self.assertGreaterEqual(len(scanner.details('PGEL')['candles']),2)
        self.assertEqual(len(FakeKite.instance.history_calls),1)
        feed.hydrate_history('PGEL')
        self.assertEqual(len(FakeKite.instance.history_calls),1)  # cache hit
        feed.close()
        self.assertTrue(FakeTicker.instance.closed)

    def test_cumulative_volume_deltas_and_new_session(self):
        s=Scanner(demo=False)
        p=dict(symbol='PGEL',price=500,prev_close=490,open=495,volume=1500,
               timestamp='2026-10-20T09:30:00+05:30')
        s.ingest_kite_cash(p)
        self.assertEqual(s.records['PGEL']['candles'][-1]['volume'],0)  # Unknown starting delta
        s.ingest_kite_cash({**p,'volume':1600,'price':501,'timestamp':'2026-10-20T09:31:00+05:30'})
        self.assertEqual(s.records['PGEL']['candles'][-1]['volume'],100)
        s.ingest_kite_cash({**p,'volume':100,'price':505,'timestamp':'2026-10-21T09:15:00+05:30'})
        self.assertEqual(s.records['PGEL']['candles'][-1]['volume'],0)  # Volume counter reset
        s.ingest_kite_cash({**p,'volume':200,'price':506,'timestamp':'2026-10-20T09:31:30+05:30'})
        self.assertEqual(s.records['PGEL']['price'],505)  # stale tick ignored


if __name__=='__main__':
    unittest.main()
