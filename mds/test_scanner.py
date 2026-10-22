"""Run regression tests: python -m unittest -v"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from sectors import ALL_SYMBOLS, PRIMARY_SECTORS, SECTOR_DEFINITIONS, SYMBOL_TO_SECTOR
from server import Scanner, candle_vwap, find_order_blocks, resample_candles, SUPPORTED_TIMEFRAMES, app


class MarketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scanner = Scanner(demo=True)

    def test_exact_universe(self):
        self.assertEqual(len(ALL_SYMBOLS), 198)
        self.assertEqual(len(PRIMARY_SECTORS), 14)
        self.assertIn('PGEL', ALL_SYMBOLS)
        self.assertEqual(SYMBOL_TO_SECTOR['PGEL'], 'DURABLES')
        self.assertEqual(len(ALL_SYMBOLS), len(set(ALL_SYMBOLS)))
        self.assertEqual(len(SECTOR_DEFINITIONS['NIFTY_50']), 50)

    def test_breadth_and_rankings(self):
        snap = self.scanner.snapshot()
        self.assertEqual(snap['trackedCount'], len(ALL_SYMBOLS))
        self.assertEqual(snap['bullishCount']+snap['bearishCount'], len(ALL_SYMBOLS))
        self.assertEqual(len(snap['bullish']), 6)
        self.assertEqual(len(snap['bearish']), 6)
        self.assertEqual(len(snap['bullishPicks']), 3)
        self.assertEqual([r['rank'] for r in snap['bullish']], list(range(1, 7)))
        self.assertEqual([r['rank'] for r in snap['bearish']], list(range(1, 7)))
        self.assertEqual(len(snap['bearishPicks']), 3)
        self.assertEqual(snap['bullishPicks'][0]['symbol'], 'PGEL')
        self.assertTrue(all(r['score']>=0 for r in snap['bullish']))

    def test_top_six_are_the_only_pick_candidates(self):
        """Both directions must select up to three distinct-sector names
        from their six displayed daily-strength leaders, not the full universe.
        """
        state = self.scanner.snapshot()
        for direction in ('bullish', 'bearish'):
            leaders = state[direction]
            picks = state[direction + 'Picks']
            self.assertLessEqual(len(leaders), 6)
            self.assertLessEqual(len(picks), 3)
            self.assertTrue({r['symbol'] for r in picks}.issubset(
                {r['symbol'] for r in leaders}))
            self.assertEqual([r['rank'] for r in leaders], list(range(1, len(leaders) + 1)))

    def test_picks_sector_diversity(self):
        snap = self.scanner.snapshot()
        for key in ('bullishPicks','bearishPicks'):
            sectors = [r['sector'] for r in snap[key]]
            self.assertEqual(len(set(sectors)), len(sectors))

    def test_five_min_candles_and_ob(self):
        details = self.scanner.details('PGEL')
        self.assertEqual(details['timeframe'], '5m')
        self.assertEqual(len(details['candles']), 68)
        self.assertTrue(details['blocks'])
        self.assertIn('order', details['disclaimer'].lower())
        for c in details['candles']:
            self.assertGreaterEqual(c['high'], max(c['open'], c['close']))
            self.assertLessEqual(c['low'], min(c['open'], c['close']))
            self.assertEqual(datetime.fromisoformat(c['time']).minute%5,0)
        for block in details['blocks']:
            self.assertLessEqual(block['low'], block['high'])
            self.assertIn(block['type'],('bullish','bearish'))

    def test_vwap_weighted(self):
        cs=[dict(open=100,high=110,low=90,close=100,volume=100),
            dict(open=120,high=130,low=110,close=120,volume=300)]
        updated=candle_vwap(cs)
        self.assertAlmostEqual(updated[0]['vwap'],100)
        self.assertAlmostEqual(updated[1]['vwap'],115)

    def test_multitimeframe_aggregation_uses_nse_session_anchor(self):
        # The 1h market bars are 09:15–10:15, 10:15–11:15 (not 10:00–11:00).
        def candle(day, hh, mm, opening, closing, vol, vwap):
            return {'time':f'{day}T{hh:02d}:{mm:02d}:00+05:30',
                    'open':opening, 'high':max(opening,closing)+1,
                    'low':min(opening,closing)-1,'close':closing,'volume':vol,'vwap':vwap}
        source=[candle('2026-10-19',9,15,100,101,10,100),
                candle('2026-10-19',10,10,101,105,30,103),
                candle('2026-10-19',10,15,105,102,20,102),
                candle('2026-10-20',9,15,102,104,40,104)]
        result=resample_candles(source,'1h')
        self.assertEqual(len(result),3)
        self.assertEqual([r['time'][11:16] for r in result],['09:15','10:15','09:15'])
        self.assertEqual(result[0]['volume'],40)
        self.assertEqual(result[0]['open'],100)
        self.assertEqual(result[0]['high'],106)
        self.assertEqual(result[0]['low'],99)
        self.assertEqual(result[0]['close'],105)
        self.assertEqual(result[0]['vwap'],103)  # VWAP of the final constituent 5m bar
        for tf, expected in [('5m',4),('15m',4),('30m',4)]:
            self.assertEqual(len(resample_candles(source,tf)),expected)
        with self.assertRaises(ValueError):
            resample_candles(source,'2h')

    def test_multi_timeframe_detail_and_validation(self):
        self.assertEqual(tuple(SUPPORTED_TIMEFRAMES),('5m','15m','30m','1h'))
        lengths=[]
        for tf in SUPPORTED_TIMEFRAMES:
            detail=self.scanner.details('PGEL',tf)
            self.assertEqual(detail['timeframe'],tf)
            lengths.append(len(detail['candles']))
            self.assertIn('vwap',detail['levels'])
            for block in detail['blocks']:
                self.assertLess(block['fromIndex'],len(detail['candles']))
                self.assertLessEqual(block['toIndex'],len(detail['candles'])-1)
        self.assertEqual(lengths[0],68)
        self.assertTrue(all(a>=b for a,b in zip(lengths,lengths[1:])))
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            self.scanner.details('PGEL','2h')
        self.assertEqual(raised.exception.status_code,400)

    def test_multitimeframe_http_api(self):
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            for tf in SUPPORTED_TIMEFRAMES:
                reply=client.get('/api/symbol/PGEL',params={'timeframe':tf})
                self.assertEqual(reply.status_code,200)
                self.assertEqual(reply.json()['timeframe'],tf)
            self.assertEqual(client.get('/api/symbol/PGEL',params={'timeframe':'7m'}).status_code,400)
            self.assertEqual(client.get('/api/symbol/PGEL').json()['timeframe'],'5m')

    def test_live_ingestion_buckets_and_oi(self):
        engine = Scanner(demo=False)
        engine.ingest_quote(dict(symbol='PGEL',price=500,prev_close=490,volume_increment=250,
                                 oi=1100,oi_base=1000,rvol=2,
                                 timestamp='2026-10-20T10:02:30+05:30'))
        engine.ingest_quote(dict(symbol='PGEL',price=501,prev_close=490,volume_increment=80,
                                 oi=1150,oi_base=1000,rvol=2,
                                 timestamp='2026-10-20T10:04:20+05:30'))
        self.assertEqual(len(engine.details('PGEL')['candles']),1)
        self.assertEqual(engine.details('PGEL')['candles'][0]['volume'],330)
        engine.ingest_quote(dict(symbol='PGEL',price=498,prev_close=490,volume_increment=100,
                                 timestamp='2026-10-20T10:05:05+05:30'))
        self.assertEqual(len(engine.details('PGEL')['candles']),2)
        self.assertEqual(engine.details('PGEL')['oiChange'],15)
        self.assertEqual(engine.snapshot()['trackedCount'],1)


if __name__ == '__main__':
    unittest.main()
