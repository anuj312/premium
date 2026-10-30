import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from server import Scanner
from kite_feed import KiteFeed, cash_payload
from history_intelligence import live_features
from stable_ob_picks import update_members, eligible_ob_pick
from test_kite_feed import FakeKite, FakeTicker
IST=ZoneInfo('Asia/Kolkata')
NOW=datetime(2026,10,30,10,21,tzinfo=IST)
class ClockMeta(type):
    def __instancecheck__(cls, obj): return isinstance(obj, datetime)
class Clock(datetime, metaclass=ClockMeta):
    @classmethod
    def now(cls,tz=None): return NOW if tz else NOW.replace(tzinfo=None)
def bars(minutes):
    return [dict(date=NOW.replace(hour=9,minute=15)+timedelta(minutes=i),
        open=100,high=102,low=99,close=101,volume=minutes*100) for i in range(0,66,minutes)]
class ReliabilityTests(unittest.TestCase):
    def make_feed(self):
        s=Scanner(False)
        s.ingest_kite_cash(dict(symbol='PGEL',price=101,prev_close=100,volume=6600,
            timestamp=NOW.isoformat(),timestamp_verified=True,received_at=NOW.isoformat()))
        f=KiteFeed(s,'test','test',kite_cls=FakeKite,ticker_cls=FakeTicker)
        f.cash={'PGEL':111}; f.reverse_cash={111:'PGEL'}
        f.rate_limited_history=lambda token,start,end,interval: bars(1 if interval=='minute' else 5)
        return s,f
    def test_missing_timestamp(self):
        self.assertIsNone(cash_payload('PGEL',dict(last_price=100,ohlc={'close':99})))
    def test_volume_cutoff(self):
        b=dict(targetDate='2026-10-30',intradaySessions=20,volumeProfile={'0':100,'5':200},adr20=2)
        q=NOW.replace(hour=9,minute=21)
        def calc(cutoff): return live_features(b,101,100,99999,q,completed_volume=150,volume_cutoff=cutoff)['rvol']
        self.assertEqual(calc('2026-10-30T09:20:00+05:30'),1.5)
        self.assertIsNone(calc(None))
        self.assertIsNone(calc('2026-10-30T09:25:00+05:30'))
    def test_ready_needs_momentum_and_invalid_members_leave(self):
        row=dict(symbol='A',change=2,obQualityScore=80,obState='READY',obDirection='bullish',obZone={'invalidated':False},pickEligible=False)
        self.assertFalse(eligible_ob_pick(row,'bullish',market_live=True,demo=False))
        names,_,_=update_members(['A'],None,[],{'A':row},revision=1,last_revision=0,direction='bullish',market_live=True,demo=False)
        self.assertEqual(names,[])
    @patch('server.datetime',Clock)
    @patch('kite_feed.datetime',Clock)
    def test_recovery_without_chart_click_and_reconnect(self):
        s,f=self.make_feed();f.recover_signal_history('PGEL');r=s.records['PGEL']
        self.assertTrue(r['historyRecovered']);self.assertEqual(r['completedVolume'],6500)
        self.assertEqual(r['volumeCutoff'],'2026-10-30T10:20:00+05:30')
        self.assertGreaterEqual(len(r['minuteTape']),60)
        f.start_websocket();self.assertFalse(r['historyRecovered']);self.assertEqual(f.signal_hydrated,{})
        f.recover_signal_history('PGEL');self.assertTrue(r['historyRecovered'])
    @patch('server.datetime',Clock)
    @patch('kite_feed.datetime',Clock)
    def test_missing_minute_blocks_recovery(self):
        s,f=self.make_feed()
        f.rate_limited_history=lambda token,start,end,interval: bars(1)[1:] if interval=='minute' else bars(5)
        f.recover_signal_history('PGEL');self.assertFalse(s.records['PGEL'].get('historyRecovered'))
    @patch('server.datetime',Clock)
    @patch('kite_feed.datetime',Clock)
    def test_checkpoint_and_rollover(self):
        s,f=self.make_feed()
        with tempfile.TemporaryDirectory() as tmp:
            f.cache_dir=Path(tmp);f.checkpoint_session();saved=list(s.records['PGEL']['minuteTape'])
            s.records['PGEL']['minuteTape']=[];f.restore_session()
            self.assertEqual(s.records['PGEL']['minuteTape'],saved)
            s.records['PGEL'].update(historyRecovered=True,completedVolume=6500,volumeCutoff=NOW.isoformat())
            s.ingest_kite_cash(dict(symbol='PGEL',price=101,prev_close=100,volume=100,timestamp=(NOW+timedelta(days=1)).isoformat(),timestamp_verified=True))
            self.assertFalse(s.records['PGEL']['historyRecovered']);self.assertIsNone(s.records['PGEL']['completedVolume'])
    @patch('server.datetime',Clock)
    def test_stale_symbol_blocked(self):
        s,f=self.make_feed()
        s.records['PGEL'].update(historyRecovered=True,volumeCutoff=NOW.replace(minute=20).isoformat(),updated=(NOW-timedelta(seconds=45)).isoformat())
        with patch('server.MARKET_MODE','kite'): row=s.public_record(s.records['PGEL'])
        self.assertTrue(row['pickDataStale']);self.assertFalse(row['pickEligible'])
