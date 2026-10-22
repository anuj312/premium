"""Fresh-momentum regression tests for the known opening-spike failure mode."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fresh_momentum import fresh_momentum, update_minute_tape, TAPE_LIMIT
from server import Scanner

IST=ZoneInfo('Asia/Kolkata')


def make_tape(prices, volumes=None, end=None):
    end=(end or datetime.now(IST)).replace(second=0,microsecond=0)
    start=end-timedelta(minutes=len(prices)-1)
    tape=[]
    for i,price in enumerate(prices):
        tape=update_minute_tape(tape,start+timedelta(minutes=i),price,
                                volumes[i] if volumes is not None else 1000+300*i)
    return tape,end


class FreshTests(unittest.TestCase):
    def test_early_8_percent_spike_that_flattens_does_not_remain_active(self):
        tape,now=make_tape([100]+[108]*20)
        result=fresh_momentum(tape,108,8.0,adr20=3.0,rvol20=3.5,now=now)
        self.assertEqual(result['momentumState'],'CONSOLIDATING')
        self.assertFalse(result['pickEligible'])
        self.assertAlmostEqual(result['recent5mPct'],0)
        self.assertAlmostEqual(result['recent15mPct'],0)

    def test_actual_fresh_5_and_15_min_move_can_qualify(self):
        prices=[100+0.25*i for i in range(21)]
        tape,now=make_tape(prices)
        result=fresh_momentum(tape,prices[-1],5.0,adr20=3,rvol20=2,now=now)
        self.assertEqual(result['momentumState'],'ACTIVE')
        self.assertGreater(result['recent5mPct'],0.1)
        self.assertGreater(result['recent15mPct'],0.22)
        self.assertTrue(result['pickEligible'])
        self.assertGreater(result['freshScore'],0)

    def test_fading_when_short_term_reverses_while_day_remains_green(self):
        prices=[100]+[109]*15+[108.8,108.6,108.4,108.2,108]
        tape,now=make_tape(prices)
        result=fresh_momentum(tape,108,8,adr20=3,rvol20=3,now=now)
        self.assertEqual(result['momentumState'],'FADING')
        self.assertFalse(result['pickEligible'])

    def test_bearish_confirmation(self):
        prices=[100-.25*i for i in range(21)]
        tape,now=make_tape(prices)
        result=fresh_momentum(tape,95,-5,adr20=4,rvol20=1.8,now=now)
        self.assertEqual(result['momentumState'],'ACTIVE')
        self.assertTrue(result['pickEligible'])
        self.assertLess(result['recent5mPct'],0)

    def test_missing_history_does_not_fabricate_volume_confirmation(self):
        prices=[100+.25*i for i in range(21)]
        tape,now=make_tape(prices)
        result=fresh_momentum(tape,105,5,rvol20=None,now=now)
        self.assertEqual(result['momentumState'],'ACTIVE')
        self.assertFalse(result['pickEligible'])
        self.assertIn('not ready',result['freshReason'])
        weak=fresh_momentum(tape,105,5,rvol20=.45,now=now)
        self.assertFalse(weak['pickEligible'])

    def test_first_fifteen_minutes_and_restart_are_not_confirmed(self):
        tape,now=make_tape([100,101,102,103,104,105])
        result=fresh_momentum(tape,105,5,rvol20=2,now=now)
        self.assertEqual(result['momentumState'],'WARMING_UP')
        self.assertFalse(result['pickEligible'])
        stale=fresh_momentum(tape,105,5,rvol20=2,now=now+timedelta(minutes=10))
        self.assertEqual(stale['momentumState'],'STALE')

    def test_fresh_tape_ends_each_day_and_is_memory_bounded(self):
        end=datetime.now(IST).replace(second=0,microsecond=0)
        tape,_=make_tape([100+i for i in range(70)],end=end)
        self.assertEqual(len(tape),TAPE_LIMIT)
        next_day=end+timedelta(days=1)
        tape=update_minute_tape(tape,next_day,102,55)
        self.assertEqual(len(tape),1)
        self.assertEqual(tape[0]['volume'],55)

    def test_live_snapshot_skips_stale_opening_leader_but_chooses_new_runner(self):
        scanner=Scanner(demo=False)
        end=datetime.now(IST).replace(second=0,microsecond=0)
        start=end-timedelta(minutes=20)
        curves={
            'PGEL':[100]+[108]*20,  # +8% daily but sideways for last 15m
            'DIXON':[100+.30*i for i in range(21)],  # +6% and still accelerating
            'BHEL':[100+.20*i for i in range(21)],  # +4% and active, distinct sector
        }
        for symbol,prices in curves.items():
            for i,p in enumerate(prices):
                scanner.ingest_quote(dict(symbol=symbol,price=p,prev_close=100,rvol=2.5,
                    volume=1500+1000*i,timestamp=(start+timedelta(minutes=i)).isoformat()))
        snap=scanner.snapshot()
        self.assertEqual(snap['bullish'][0]['symbol'],'DIXON')  # fresh runner overtakes early spike
        self.assertEqual(snap['bullish'][0]['momentumState'],'ACTIVE')
        self.assertEqual(next(r for r in snap['bullish'] if r['symbol']=='PGEL')['momentumState'],'CONSOLIDATING')
        picked=[r['symbol'] for r in snap['bullishPicks']]
        self.assertEqual(picked[:2],['DIXON','BHEL'])
        self.assertFalse(snap['bullishPicks'][-1]['pickEligible'])
        self.assertEqual(len(picked),3)  # always show ranked candidates
        self.assertEqual(snap['bullishPicks'][-1]['symbol'],'PGEL')
        self.assertFalse(snap['bullishPicks'][-1]['pickConfirmed'])
        self.assertEqual(snap['bullishPicks'][-1]['pickStatus'],'SIDEWAYS')
        # Check newly arrived history has no impact on the fresh price movements.
        scanner.set_baseline('PGEL',{'targetDate':end.date().isoformat(), 'sessions':20,
           'intradaySessions':0,'adr20':3,'atr14':1,'volumeProfile':{}})
        self.assertFalse(scanner.public_record(scanner.records['PGEL'])['pickEligible'])


if __name__=='__main__':
    unittest.main()
