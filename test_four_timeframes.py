"""Regression tests for 5m+15m+30m+1h same-session adaptive momentum."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fresh_momentum import fresh_momentum, update_minute_tape, TAPE_LIMIT
from momentum_rating import momentum_rating
from adaptive_ranking import adaptive_rank

IST = ZoneInfo('Asia/Kolkata')
END = datetime(2026, 10, 22, 13, 30, tzinfo=IST)


def tape_for(prices, now=END):
    tape = []
    for i, price in enumerate(prices):
        tape = update_minute_tape(tape, now-timedelta(minutes=len(prices)-1-i),
                                  price, 50000+i*1200)
    return tape


class FourHorizonTests(unittest.TestCase):
    def test_twenty_minutes_uses_only_observed_short_windows(self):
        prices = [100+i*.22 for i in range(21)]
        a = fresh_momentum(tape_for(prices), prices[-1], 4, adr20=4,
                           rvol20=2.5, now=END)
        self.assertEqual(a['momentumState'], 'ACTIVE')
        self.assertTrue(a['pickEligible'])
        self.assertEqual(a['momentumWindowsReady'], 2)
        self.assertIsNone(a['recent30mPct'])
        self.assertIsNone(a['recent60mPct'])
        row = dict(a, change=4, price=104.4, vwap=102, adr20=4,
                   rvol=2.5, historySessions=20, intradaySessions=20)
        score = momentum_rating(row)
        self.assertEqual(score['momentumScoreBreakdown']['recent30m'], 0)
        self.assertEqual(score['momentumScoreBreakdown']['recent60m'], 0)
        self.assertLessEqual(score['momentumScore100'], 75)

    def test_thirty_available_before_one_hour(self):
        prices = [100+i*.20 for i in range(36)]
        a = fresh_momentum(tape_for(prices), prices[-1], 7, adr20=4,
                           rvol20=2.3, now=END)
        self.assertTrue(a['pickEligible'])
        self.assertEqual(a['momentumWindowsReady'], 3)
        self.assertGreater(a['recent30mPct'], 0)
        self.assertIsNone(a['recent60mPct'])

    def test_four_observed_windows_bullish_and_bearish(self):
        for sign in (+1, -1):
            prices = [100 + sign*i*.15 for i in range(66)]
            result = fresh_momentum(tape_for(prices),prices[-1],sign*9.75,
                                    adr20=3,rvol20=2,now=END)
            self.assertEqual(result['momentumWindowsReady'], 4)
            self.assertEqual(result['momentumState'], 'ACTIVE')
            self.assertTrue(result['pickEligible'])
            self.assertEqual(result['recent60mPct'] > 0, sign == 1)

    def test_opposing_hourly_trend_blocks_newly_rising_short_windows(self):
        prices = [120-.2*i for i in range(51)] + [110+.6*i for i in range(1,16)]
        a = fresh_momentum(tape_for(prices),prices[-1],19.0,adr20=4,
                           rvol20=3,now=END)
        self.assertGreater(a['recent5mPct'],0)
        self.assertGreater(a['recent15mPct'],0)
        self.assertEqual(a['momentumWindowsReady'],4)
        self.assertFalse(a['pickEligible'])
        self.assertEqual(a['momentumState'],'CONSOLIDATING')

    def test_early_spike_stops_dominating_after_hour(self):
        prices = [100,108]+[108]*64
        a = fresh_momentum(tape_for(prices),108,8,adr20=3,
                           rvol20=3,now=END)
        self.assertEqual(a['momentumWindowsReady'],4)
        self.assertFalse(a['pickEligible'])
        self.assertEqual(a['momentumState'],'CONSOLIDATING')
        flat = {**a, 'change':8, 'sessionMovePct':8, 'rvol':3,'price':108,
                'vwap':106, 'adr20':3, 'historySessions':20,'intradaySessions':20}
        flat.update(momentum_rating(flat))
        self.assertLessEqual(flat['momentumScore100'],42)
        fresh = {**flat,'momentumState':'ACTIVE','pickEligible':True,
                 'recent5mPct':1,'recent15mPct':2,'recent30mPct':3,
                 'recent60mPct':5,'sessionMovePct':2,'change':2}
        fresh.update(momentum_rating(fresh))
        self.assertGreater(adaptive_rank(fresh),adaptive_rank(flat))

    def test_tape_bounded_and_rollover_clears_previous_session(self):
        tape = tape_for([100+i*.1 for i in range(130)])
        self.assertEqual(len(tape),TAPE_LIMIT)
        tomorrow = END+timedelta(days=1)
        tape = update_minute_tape(tape,tomorrow,108,2000)
        self.assertEqual(len(tape),1)
        result = fresh_momentum(tape,108,8,rvol20=3,now=tomorrow)
        self.assertIsNone(result['recent60mPct'])
        self.assertEqual(result['momentumWindowsReady'],0)

    def test_ui_shows_all_four_without_fabricating_pending(self):
        from pathlib import Path
        js=(Path(__file__).parent/'static/app.js').read_text()
        for field in ['recent5mPct','recent15mPct','recent30mPct','recent60mPct']:
            self.assertIn('recentChange(r.'+field+')',js)
        self.assertIn('momentumWindowsReady',js)
        self.assertIn('momentum-four-timeframes.css',
                      (Path(__file__).parent/'static/index.html').read_text())


if __name__ == '__main__': unittest.main()
