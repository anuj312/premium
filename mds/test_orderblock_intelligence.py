"""Deterministic candle-only setup confirmation and API regression tests."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from orderblock_intelligence import analyze_order_block
from server import Scanner, app

IST = ZoneInfo('Asia/Kolkata')
START=datetime(2026,10,20,9,15,tzinfo=IST)

def bar(n,o,h,l,c,v=1000):
    return {'time':(START+timedelta(minutes=5*n)).isoformat(),
            'open':o,'high':h,'low':l,'close':c,'volume':v,'vwap':100.0}

def setup_bars():
    bars=[]
    for i in range(10):
        # Narrow, no structural breaks; bears an obvious sweep at anchor index 10.
        bars.append(bar(i,100.0,101.2,99.0,100.4 if i%2 else 99.8))
    bars += [bar(10,100,101,97.0,99.2,1300),  # opposing + wick through 99 (sweep)
             bar(11,99.2,107,98.8,106.4,6000), # strong volume BOS above previous high
             bar(12,106.4,108.2,103.0,107.8,2200), # valid bullish FVG from idx10
             bar(13,101.1,107,98.5,106.5,2400)] # retest then bullish close above zone
    return bars

def fifteen():
    start=datetime(2026,10,20,8,45,tzinfo=IST)
    return [{'time':(start+timedelta(minutes=15*i)).isoformat(),
             'open':100+i,'high':102+i,'low':99+i,'close':100.5+i,
             'volume':2000,'vwap':100} for i in range(6)]

class OBQualityTests(unittest.TestCase):
    def test_insufficient_history_is_wait_without_fabricated_score(self):
        x=analyze_order_block(setup_bars()[:9],now=START+timedelta(minutes=70))
        self.assertEqual(x['obState'],'WAIT');self.assertIsNone(x['obQualityScore'])
        self.assertIsNone(x['obZone'])

    def test_confirmed_bos_fvg_sweep_and_retest_can_be_ready(self):
        x=analyze_order_block(setup_bars(),candles15=fifteen(),now=START+timedelta(minutes=70))
        self.assertIsNotNone(x['obZone'],x)
        self.assertEqual(x['obDirection'],'bullish');self.assertEqual(x['obState'],'READY')
        self.assertTrue(x['obBos']);self.assertTrue(x['obFvg']);self.assertTrue(x['obSweep'])
        self.assertGreaterEqual(x['obQualityScore'],60)
        self.assertLessEqual(x['obQualityScore'],100)
        self.assertEqual(round(sum(x['obBreakdown'].values())),x['obQualityScore'])
        self.assertIn('UNAVAILABLE',x['obDeltaSource'])

    def test_mirrored_bearish_bos_fvg_sweep_and_retest_ready(self):
        def mirror(row):
            return {**row, 'open':200-row['open'], 'close':200-row['close'],
                    'high':200-row['low'], 'low':200-row['high'],
                    'vwap':200-row.get('vwap',100)}
        source=[mirror(x) for x in setup_bars()]
        context=[mirror(x) for x in fifteen()]
        x=analyze_order_block(source,candles15=context,now=START+timedelta(minutes=70))
        self.assertEqual(x['obState'],'READY',x)
        self.assertEqual(x['obDirection'],'bearish')
        self.assertEqual(x['obZone']['type'],'bearish')
        self.assertEqual(x['obFvg']['type'],'bearish')
        self.assertEqual(x['obSweep']['type'],'buy_side_sweep')
        self.assertTrue(0 <= x['obQualityScore'] <= 100)

    def test_no_retest_means_wait_not_ready(self):
        x=analyze_order_block(setup_bars()[:-1],candles15=fifteen(),now=START+timedelta(minutes=65))
        self.assertEqual(x['obState'],'WAIT');self.assertIsNotNone(x['obZone'])
        self.assertLessEqual(x['obQualityScore'],100)

    def test_invalidated_by_completed_close(self):
        b=setup_bars()+[bar(14,97,106,95,96,3000)]
        x=analyze_order_block(b,candles15=fifteen(),now=START+timedelta(minutes=75))
        self.assertEqual(x['obState'],'INVALID')
        self.assertLessEqual(x['obQualityScore'],25)

    def test_partial_candle_does_not_trigger_ready(self):
        b=setup_bars()
        x=analyze_order_block(b,candles15=fifteen(),now=START+timedelta(minutes=65+2))
        # 10:20–10:25 retest bar is still forming: never signal READY.
        self.assertEqual(x['obState'],'WAIT')

    def test_stale_candles_never_ready_even_if_good_structure(self):
        x=analyze_order_block(setup_bars(),candles15=fifteen(),now=START+timedelta(minutes=150))
        self.assertEqual(x['obState'],'WAIT')
        self.assertIn('stale',x['obReason'].lower())

    def test_demo_scanner_returns_ob_separately_from_momentum(self):
        scanner=Scanner(demo=True);snapshot=scanner.snapshot()
        for pick in snapshot['bullishPicks']+snapshot['bearishPicks']:
            self.assertIn('obQualityScore',pick)
            self.assertIn('obState',pick)
            self.assertIn('momentumScore100',pick)
        for tf in ('5m','15m','30m','1h'):
            d=scanner.details('PGEL',tf)
            self.assertEqual(d['timeframe'],tf)
            self.assertEqual(d['obBasis'],'SIMULATED')
            self.assertIn(d['obState'],('WAIT','READY','INVALID'))
            self.assertEqual(d['obDeltaSource'].split(' ')[0],'UNAVAILABLE')

    def test_http_details_contract(self):
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            r=client.get('/api/symbol/PGEL?timeframe=5m')
            self.assertEqual(r.status_code,200)
            d=r.json()
            self.assertIn('obQualityScore',d)
            self.assertIn('obBreakdown',d)
            self.assertIn('obBos',d)
            self.assertIn('obFvg',d)
            self.assertIn('obSweep',d)

if __name__=='__main__':unittest.main()
