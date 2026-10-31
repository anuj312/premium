"""Deterministic no-network tests for twenty-session features and cache."""
import tempfile
import unittest
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from history_intelligence import make_baseline, live_features, save_cache, load_cache
from server import Scanner

IST = ZoneInfo('Asia/Kolkata')


def sample_history(target=date(2026,10,20), n=21):
    days = []
    cursor = target-timedelta(days=1)
    while len(days) < n:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    daily=[]; intraday=[]
    for i,d in enumerate(days):
        daily.append({'date': datetime(d.year,d.month,d.day,15,30,tzinfo=IST),
                      'open':100,'high':105,'low':95,'close':100,'volume':7500})
        for j in range(75):
            intraday.append({'date':datetime(d.year,d.month,d.day,9,15,tzinfo=IST)+timedelta(minutes=5*j),
                             'open':100,'high':101,'low':99,'close':100,'volume':100})
    return daily,intraday


class HistoryTests(unittest.TestCase):
    def test_lookback_20_completed_sessions_exact(self):
        target=date(2026,10,20)
        daily,minute=sample_history(target)
        # Inject today's gigantic spike; it must NOT alter any baseline metric.
        daily.append({'date':datetime(2026,10,20,12,tzinfo=IST), 'open':50,'high':999,'low':1,'close':2,'volume':10**9})
        minute.append({'date':datetime(2026,10,20,9,15,tzinfo=IST),'volume':10**8})
        out=make_baseline(daily,minute,target)
        self.assertEqual(out['sessions'],20)
        self.assertEqual(out['intradaySessions'],20)
        self.assertEqual(out['status'],'READY')
        self.assertEqual(out['high20'],105)
        self.assertEqual(out['low20'],95)
        self.assertEqual(out['avgVolume20'],7500)
        self.assertEqual(out['adr20'],10)
        self.assertEqual(out['atr14'],10)
        self.assertEqual(out['volumeProfile']['0'],100)
        self.assertEqual(out['volumeProfile']['10'],300)
        self.assertEqual(out['volumeProfile']['370'],7500)
        feat=live_features(out,110,100,600,datetime(2026,10,20,9,26,tzinfo=IST))
        self.assertAlmostEqual(feat['rvol'],2.0)
        self.assertEqual(feat['breakout20'],'ABOVE_20D_HIGH')
        self.assertEqual(feat['rangeMultiple'],1.)

    def test_partial_and_new_day_invalidates_reference(self):
        d,m=sample_history(n=7)
        out=make_baseline(d,m,date(2026,10,20))
        self.assertEqual(out['status'],'PARTIAL')
        self.assertEqual(out['intradaySessions'],7)
        features=live_features(out,105,100,1200,datetime(2026,10,20,10,0,tzinfo=IST))
        self.assertIsNone(features['rvol'])
        nextday=live_features(out,105,100,1200,datetime(2026,10,21,10,0,tzinfo=IST))
        self.assertEqual(nextday['historyStatus'],'REFRESHING')
        self.assertIsNone(nextday['adr20'])

    def test_cache_roundtrip(self):
        d,m=sample_history()
        baseline=make_baseline(d,m,date(2026,10,20))
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            save_cache(directory,'PGEL',date(2026,10,20),baseline,m)
            result=load_cache(directory,'PGEL',date(2026,10,20))
            self.assertEqual(result['baseline']['sessions'],20)
            self.assertEqual(len(result['candles']),len(m))
            self.assertIsNone(load_cache(directory,'PGEL',date(2026,10,21)))

    def test_tick_reset_on_new_trading_session(self):
        scanner=Scanner(demo=False)
        stock=lambda date,price,vol:{'symbol':'PGEL','price':price,'prev_close':100,
            'volume':vol,'timestamp':date}
        scanner.ingest_kite_cash(stock('2026-10-19T15:25:00+05:30',101,10000))
        scanner.ingest_kite_cash(stock('2026-10-19T15:26:00+05:30',102,10200))
        self.assertEqual(scanner.tick_count,2)
        scanner.ingest_kite_cash(stock('2026-10-20T09:15:01+05:30',99,100))
        self.assertEqual(scanner.tick_count,1)
        self.assertEqual(scanner.session_date,'2026-10-20')
        self.assertEqual(scanner.records['PGEL']['volume'],100)
        self.assertEqual(scanner.records['PGEL']['candles'][-1]['volume'],0)
        scanner.ingest_kite_cash(stock('2026-10-20T09:16:01+05:30',100,400))
        self.assertEqual(scanner.records['PGEL']['candles'][-1]['volume'],300)
        self.assertEqual(scanner.tick_count,2)

if __name__=='__main__':unittest.main()
