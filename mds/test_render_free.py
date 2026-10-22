"""Offline regression tests for Render Free's bounded memory/history behavior."""
import asyncio
import gzip
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from history_intelligence import load_cache, save_cache, make_baseline
from kite_feed import KiteFeed
from server import Scanner
from test_kite_feed import FakeKite, FakeTicker

IST = ZoneInfo('Asia/Kolkata')


class FullHistoryKite(FakeKite):
    def historical_data(self, token, from_date, to_date, interval, oi=False):
        self.history_calls.append((token, interval))
        today = datetime.now(IST).date()
        days = []
        d = today - timedelta(days=1)
        while len(days) < 22:
            if d.weekday() < 5:
                days.append(d)
            d -= timedelta(days=1)
        days.sort()
        if interval == 'day':
            return [dict(date=datetime.combine(d, datetime.min.time(),tzinfo=IST),
                         open=95,high=105,low=95,close=100,volume=7500) for d in days]
        return [dict(date=datetime(d.year,d.month,d.day,9,15,tzinfo=IST)+timedelta(minutes=i*5),
                     open=99,high=101,low=99,close=100,volume=100)
                for d in days for i in range(75)]


class RenderFreeTests(unittest.TestCase):
    def test_bulk_cache_stores_only_small_baseline(self):
        today=datetime.now(IST).date()
        data={'version':3, 'targetDate':today.isoformat(), 'sessions':20,
              'volumeProfile':{str(i*5):float(i*100) for i in range(75)}}
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            save_cache(folder,'PGEL',today,data)
            file=next(folder.glob('*.gz'))
            self.assertLess(file.stat().st_size, 4000)
            with gzip.open(file,'rt',encoding='utf-8') as stream:
                self.assertEqual(list(json.load(stream)), ['baseline'])
            self.assertEqual(load_cache(folder,'PGEL',today)['baseline'],data)

    def test_twenty_sessions_load_only_twice_then_hit_compact_cache(self):
        scanner=Scanner(demo=False)
        feed=KiteFeed(scanner,'key','token',kite_cls=FullHistoryKite,ticker_cls=FakeTicker)
        feed.cash={'PGEL':111}
        feed.history_call_spacing=0.
        with tempfile.TemporaryDirectory() as td:
            feed.cache_dir=Path(td)
            out=feed.load_history_for_symbol('PGEL')
            self.assertEqual(out['baseline']['status'],'READY')
            self.assertEqual(out['baseline']['intradaySessions'],20)
            self.assertEqual(len(feed.kite.history_calls),2)
            again=feed.load_history_for_symbol('PGEL')
            self.assertEqual(again,out)
            self.assertEqual(len(feed.kite.history_calls),2)
            self.assertLess(sum(p.stat().st_size for p in Path(td).rglob('*.gz')), 4000)
            self.assertEqual(scanner.baselines['PGEL']['sessions'],20)

    def test_chart_history_lru_evicts_old_stock_history(self):
        scanner=Scanner(demo=False)
        scanner.max_chart_symbols=2
        old = datetime.now(IST) - timedelta(days=6)
        for symbol in ('PGEL','RELIANCE','INFY'):
            scanner.ingest_quote(dict(symbol=symbol, price=101, prev_close=100,
                                      timestamp=datetime.now(IST).isoformat()))
            history=[]
            for i in range(180):
                stamp=old+timedelta(minutes=5*i)
                history.append(dict(date=stamp,open=100,high=102,low=99,close=101,volume=100))
            scanner.seed_candles(symbol,history)
        self.assertEqual(list(scanner.chart_lru),['RELIANCE','INFY'])
        self.assertLessEqual(len(scanner.records['PGEL']['candles']),90)
        self.assertGreater(len(scanner.records['INFY']['candles']),90)

    def test_history_worker_retries_bounded_and_reports_failure(self):
        scanner=Scanner(demo=False)
        feed=KiteFeed(scanner,'key','token',kite_cls=FakeKite,ticker_cls=FakeTicker)
        feed.cash={'PGEL':111}
        feed.history_symbol_pause=0.
        feed.history_retry_delay=0.
        calls=[]
        def fail(symbol,target):
            calls.append(symbol)
            raise RuntimeError('sample transport error')
        feed.load_history_for_symbol=fail
        asyncio.run(feed.populate_all_history(datetime.now(IST).date()))
        self.assertEqual(calls,['PGEL','PGEL'])
        self.assertEqual(scanner.history_failed,1)

    def test_history_worker_stops_unauthorized_without_spamming(self):
        scanner=Scanner(demo=False)
        feed=KiteFeed(scanner,'key','token',kite_cls=FakeKite,ticker_cls=FakeTicker)
        feed.cash={'PGEL':111,'RELIANCE':222}
        class TokenException(Exception):
            pass
        calls=[]
        def denied(symbol,target):
            calls.append(symbol)
            raise TokenException('secret is not logged')
        feed.load_history_for_symbol=denied
        asyncio.run(feed.populate_all_history(datetime.now(IST).date()))
        self.assertEqual(calls,['PGEL'])
        self.assertEqual(scanner.history_failed,2)
        self.assertIn('token', scanner.history_error.lower())

if __name__=='__main__':
    unittest.main()
