"""0–100 rating display tests: eligibility, normalization, demo labeling."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from momentum_rating import momentum_rating
from server import Scanner

IST = ZoneInfo('Asia/Kolkata')


class RatingTests(unittest.TestCase):
    def row(self, **changes):
        example = dict(change=4.0, price=105.0, vwap=103.0, adr20=4.0,
            recent5mPct=0.52, recent15mPct=1.20,
            recent30mPct=1.8, recent60mPct=2.8, rvol=3.2,
            recentVolumeRatio=2.4, breakout20='ABOVE_20D_HIGH',
            momentumState='ACTIVE', pickEligible=True,
            historySessions=20, intradaySessions=20)
        example.update(changes)
        return example

    def test_full_strength_reaches_exactly_100_and_is_explained(self):
        rated = momentum_rating(self.row(recent5mPct=4, recent15mPct=5,
                                          recent30mPct=8, recent60mPct=10, rvol=8,
                                          recentVolumeRatio=7))
        self.assertEqual(rated['momentumScore100'], 100)
        self.assertEqual(rated['momentumScoreBasis'], 'LIVE_20D')
        self.assertEqual(round(sum(rated['momentumScoreBreakdown'].values())), 100)

    def test_middle_strength_and_weak_activity_get_lower_values(self):
        middle = momentum_rating(self.row())
        weak = momentum_rating(self.row(recent5mPct=.23, recent15mPct=.48,
                                          rvol=1.1, recentVolumeRatio=None,
                                          breakout20=None, vwap=106))
        self.assertGreater(middle['momentumScore100'], weak['momentumScore100'])
        self.assertGreater(middle['momentumScore100'], 55)
        self.assertLess(weak['momentumScore100'], 50)

    def test_bearish_uses_negative_direction_and_below_vwap(self):
        bear = momentum_rating(self.row(change=-3.5,price=95,vwap=98,
                    recent5mPct=-.52, recent15mPct=-1.2,
                    recent30mPct=-1.8, recent60mPct=-2.8,
                    breakout20='BELOW_20D_LOW'))
        self.assertEqual(bear['momentumScoreBreakdown']['vwapAlignment'], 7)
        self.assertEqual(bear['momentumScoreBreakdown']['breakout20'], 3)
        wrong = momentum_rating(self.row(change=-3.5,price=95,vwap=94,
                    recent5mPct=-.52, recent15mPct=-1.2,
                    recent30mPct=-1.8, recent60mPct=-2.8,
                    breakout20='ABOVE_20D_HIGH'))
        self.assertEqual(wrong['momentumScoreBreakdown']['vwapAlignment'], 0)
        self.assertEqual(wrong['momentumScoreBreakdown']['breakout20'], 0)

    def test_watch_scores_are_bounded_and_marked_unconfirmed(self):
        flat = momentum_rating(self.row(momentumState='CONSOLIDATING'))
        self.assertLessEqual(flat['momentumScore100'], 42)
        self.assertEqual(flat['momentumScoreBasis'], 'LIVE_WATCH')
        fading = momentum_rating(self.row(momentumState='FADING'))
        self.assertLessEqual(fading['momentumScore100'], 25)
        missing = momentum_rating(self.row(pickEligible=False,rvol=None))
        self.assertLessEqual(missing['momentumScore100'], 55)
        self.assertEqual(missing['momentumScoreBasis'], 'LIVE_WATCH')
        stale = momentum_rating(self.row(momentumState='STALE'))
        self.assertIsNone(stale['momentumScore100'])
        self.assertEqual(stale['momentumScoreBasis'], 'STALE')

    def test_partial_history_is_explicit(self):
        score=momentum_rating(self.row(historySessions=17,intradaySessions=10))
        self.assertEqual(score['momentumScoreBasis'], 'LIVE_PARTIAL_HISTORY')
        self.assertIsInstance(score['momentumScore100'], int)

    def test_demo_score_is_bounded_and_flagged_as_simulated(self):
        score=momentum_rating(dict(change=8.19,rvol=3,oiChange=10,
                                    price=527,vwap=500),demo=True)
        self.assertGreater(score['momentumScore100'], 70)
        self.assertLessEqual(score['momentumScore100'],100)
        self.assertEqual(score['momentumScoreBasis'],'DEMO')

    def test_scanner_demo_top_picks_have_score_and_detail_matches(self):
        scan=Scanner(demo=True)
        snap=scan.snapshot()
        picked=(snap['bullishPicks']+snap['bearishPicks'])
        self.assertGreater(len(picked),0)
        self.assertTrue(all(0 <= s['momentumScore100'] <= 100 for s in picked))
        self.assertTrue(all(s['momentumScoreBasis']=='DEMO' for s in picked))
        stock=picked[0]['symbol']
        self.assertEqual(scan.details(stock)['momentumScore100'],picked[0]['momentumScore100'])

    def test_early_spike_score_decays_even_with_big_day_gain(self):
        from fresh_momentum import fresh_momentum, update_minute_tape
        now=datetime.now(IST).replace(second=0,microsecond=0)
        tape=[]
        for i in range(21):
            tape=update_minute_tape(tape,now-timedelta(minutes=20-i),
                100 if i==0 else 108, 1200+i*150)
        f=fresh_momentum(tape,108,8,adr20=3,rvol20=4,now=now)
        rating=momentum_rating(self.row(change=8.0,price=108,
                        recent5mPct=f['recent5mPct'],recent15mPct=f['recent15mPct'],
                        momentumState=f['momentumState'],pickEligible=f['pickEligible']))
        self.assertLessEqual(rating['momentumScore100'],42)
        self.assertEqual(rating['momentumScoreBasis'],'LIVE_WATCH')


if __name__=='__main__': unittest.main()
