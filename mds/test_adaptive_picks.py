"""Regression tests: visible Top 3 + no all-day opening-spike lock-in."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from server import Scanner
from adaptive_ranking import adaptive_rank, sort_picks_by_ob_score
from stable_ob_picks import eligible_ob_pick, REPLACEMENT_UPDATES

IST = ZoneInfo('Asia/Kolkata')


class AdaptivePickTests(unittest.TestCase):
    def test_sideways_early_spike_loses_rank_to_fresh_mover(self):
        old = {'change': 9, 'sessionMovePct': 9, 'momentumState': 'CONSOLIDATING',
               'momentumScore100': 38, 'volumeConfirmed': True}
        fresh = {'change': 1.4, 'sessionMovePct': 1.4, 'momentumState': 'ACTIVE',
                 'momentumScore100': 68, 'volumeConfirmed': True}
        self.assertGreater(adaptive_rank(fresh), adaptive_rank(old))
        self.assertLessEqual(adaptive_rank(old), 46)  # Opening spike capped

    def test_top_three_selected_by_ob_not_momentum_or_sector(self):
        rows = [dict(symbol='A', sector='AUTO', pickEligible=False,
                     momentumScore100=91, obQualityScore=25, adaptiveRankScore=100),
                dict(symbol='B', sector='AUTO', pickEligible=False,
                     momentumScore100=87, obQualityScore=85, adaptiveRankScore=90),
                dict(symbol='C', sector='IT', pickEligible=True,
                     momentumScore100=65, obQualityScore=None, adaptiveRankScore=75),
                dict(symbol='D', sector='AUTO', pickEligible=False,
                     momentumScore100=30, obQualityScore=99, adaptiveRankScore=10)]
        self.assertEqual([r['symbol'] for r in sort_picks_by_ob_score(rows)[:3]],
                         ['D', 'B', 'A'])

    def test_missing_ob_last_and_ob_ties_broken_by_momentum(self):
        rows = [dict(symbol='MISSING', obQualityScore=None, momentumScore100=99),
                dict(symbol='LOW_MOM', obQualityScore=80, momentumScore100=40),
                dict(symbol='HIGH_MOM', obQualityScore=80, momentumScore100=60),
                dict(symbol='LOW_OB', obQualityScore=12, momentumScore100=99)]
        self.assertEqual([r['symbol'] for r in sort_picks_by_ob_score(rows)],
                         ['HIGH_MOM', 'LOW_MOM', 'LOW_OB', 'MISSING'])

    @staticmethod
    def _ready_data(scanner):
        """Known, formed directional zones for each side of the live leaderboard."""
        initial = scanner.snapshot()
        sides = {d:[r['symbol'] for r in initial[d]] for d in ('bullish','bearish')}
        scores = {symbol:score for names in sides.values()
                  for symbol,score in zip(names, (90, 85, 80, 75, 70, 65))}
        directions = {symbol:d for d,names in sides.items() for symbol in names}
        def get_ob(record, status):
            symbol=record['symbol']
            score=scores.get(symbol, None)
            return {'obQualityScore':score, 'obState':'READY' if score is not None else 'WAIT',
                    'obDirection':directions.get(symbol),
                    'obZone':{'invalidated':False} if score is not None else None}
        scanner.top3_locked={'bullish':[], 'bearish':[]}
        scanner.top3_pending={'bullish':None, 'bearish':None}
        return sides, scores, get_ob

    def test_snapshot_top3_ob_quality_first_for_both_sides(self):
        from unittest.mock import patch
        scanner=Scanner(demo=True)
        sides, scores, get_ob=self._ready_data(scanner)
        with patch.object(scanner,'cached_leader_ob',side_effect=get_ob):
            snap=scanner.snapshot()
        for direction, names in sides.items():
            self.assertEqual([r['symbol'] for r in snap[direction+'Picks']],names[:3])
            self.assertEqual([r['obQualityScore'] for r in snap[direction+'Picks']],[90,85,80])
            self.assertTrue(all(r['pickStatus']=='DEMO' for r in snap[direction+'Picks']))
            self.assertTrue(all('candleProfile' in r for r in snap[direction+'Picks']))

    def test_three_fresh_revisions_needed_for_replacement_on_both_sides(self):
        from unittest.mock import patch
        scanner=Scanner(demo=True)
        sides,scores,get_ob=self._ready_data(scanner)
        with patch.object(scanner,'cached_leader_ob',side_effect=get_ob):
            start=scanner.snapshot()
            for direction in sides:
                self.assertEqual([r['symbol'] for r in start[direction+'Picks']],sides[direction][:3])
            for direction,names in sides.items():
                scores[names[4]]=99  # Better than weakest 80
            scanner.snapshot()  # Same market revision as initial selection: count=1
            pending=scanner.snapshot()  # Read-only API requests do NOT count as updates
            for direction in sides:
                self.assertEqual(pending['top3Pending'][direction]['count'],1)
                self.assertEqual(set(r['symbol'] for r in pending[direction+'Picks']),set(sides[direction][:3]))
            for count in (2,3):
                scanner.revision+=1
                current=scanner.snapshot()
                for direction,names in sides.items():
                    if count<REPLACEMENT_UPDATES:
                        self.assertEqual(current['top3Pending'][direction]['count'],count)
                        self.assertEqual(set(r['symbol'] for r in current[direction+'Picks']),set(names[:3]))
                    else:
                        self.assertIsNone(current['top3Pending'][direction])
                        self.assertEqual([r['symbol'] for r in current[direction+'Picks']],
                                         [names[4],names[0],names[1]])
                        self.assertEqual([r['obQualityScore'] for r in current[direction+'Picks']], [99,90,85])

    def test_challenger_must_stay_higher_for_consecutive_updates(self):
        from unittest.mock import patch
        scanner=Scanner(demo=True)
        sides,scores,get_ob=self._ready_data(scanner)
        bull=sides['bullish']
        with patch.object(scanner,'cached_leader_ob',side_effect=get_ob):
            scanner.snapshot()
            scores[bull[4]]=95
            scanner.revision+=1
            scanner.snapshot()
            self.assertEqual(scanner.top3_pending['bullish']['count'],1)
            scores[bull[4]]=70  # transient high reading disappears
            scanner.revision+=1
            snap=scanner.snapshot()
            self.assertIsNone(snap['top3Pending']['bullish'])
            scores[bull[4]]=95
            scanner.revision+=1
            scanner.snapshot()
            self.assertEqual(scanner.top3_pending['bullish']['count'],1)
            self.assertEqual(set(scanner.top3_locked['bullish']),set(bull[:3]))

    def test_wait_without_eligibility_cannot_replace_even_with_100_ob(self):
        from unittest.mock import patch
        scanner=Scanner(demo=True)
        sides,scores,get_ob=self._ready_data(scanner)
        with patch.object(scanner,'cached_leader_ob',side_effect=lambda rec,status:
                          ({**get_ob(rec,status), 'obState':'WAIT'} if rec['symbol'] == sides['bullish'][4]
                           else get_ob(rec,status))):
            scanner.snapshot()
            scores[sides['bullish'][4]]=100
            for _ in range(6):
                scanner.revision+=1
                snap=scanner.snapshot()
            self.assertEqual(set(scanner.top3_locked['bullish']),set(sides['bullish'][:3]))
            self.assertIsNone(snap['top3Pending']['bullish'])

    def test_wait_eligibility_rules_and_invalid_or_stale_exclusions(self):
        base={'symbol':'X', 'change':2, 'obQualityScore':85, 'obState':'WAIT',
              'obDirection':'bullish', 'obZone':{'invalidated':False},
              'pickEligible':True,'obReason':'Fresh zone identified; waiting for a completed retest and rejection candle'}
        self.assertTrue(eligible_ob_pick(base,'bullish',market_live=True,demo=False))
        self.assertFalse(eligible_ob_pick(base,'bearish',market_live=True,demo=False))
        self.assertFalse(eligible_ob_pick(base,'bullish',market_live=False,demo=False))
        for patch_data in ({'pickEligible':False},{'obQualityScore':None},
                           {'obState':'INVALID'},{'obZone':{'invalidated':True}},
                           {'obReason':'Market candles are stale; wait for fresh completed bars'},
                           {'obReason':'Order block is too old; wait for a new structure break'},
                           {'obQualityScore':55},{'obDirection':'bearish'},
                           {'pickDataStale':True}):
            self.assertFalse(eligible_ob_pick({**base,**patch_data},'bullish',market_live=True,demo=False))
        ready={**base,'obState':'READY','pickEligible':False}
        self.assertTrue(eligible_ob_pick(ready,'bullish',market_live=True,demo=False))
        self.assertTrue(eligible_ob_pick(ready,'bullish',market_live=False,demo=True))

    def test_pinned_names_remain_visible_with_missing_score_until_qualified_replacement(self):
        from unittest.mock import patch
        scanner=Scanner(demo=True)
        sides,scores,get_ob=self._ready_data(scanner)
        with patch.object(scanner,'cached_leader_ob',side_effect=get_ob):
            scanner.snapshot()
            pinned=sides['bullish'][0]
            scores[pinned]=None
            scanner.revision+=1
            snap=scanner.snapshot()
            self.assertIn(pinned,[r['symbol'] for r in snap['bullishPicks']])
            self.assertIsNone(next(r for r in snap['bullishPicks'] if r['symbol']==pinned)['obQualityScore'])
            for _ in range(2):
                scanner.revision+=1
                snap=scanner.snapshot()
            self.assertNotIn(pinned,[r['symbol'] for r in snap['bullishPicks']])
            self.assertEqual(len(snap['bullishPicks']),3)

    def test_ineligible_incumbent_with_numeric_wait_can_be_replaced(self):
        from unittest.mock import patch
        scanner=Scanner(demo=True)
        sides,scores,get_ob=self._ready_data(scanner)
        bull=sides['bullish']
        wait_holder=bull[0]
        temporarily_waiting={'value':False}
        def conditional_ob(rec,status):
            original=get_ob(rec,status)
            if rec['symbol']==wait_holder and temporarily_waiting['value']:
                return {**original, 'obState':'WAIT', 'obReason':'Order block is too old; wait for a new structure break'}
            return original
        with patch.object(scanner,'cached_leader_ob',side_effect=conditional_ob):
            scanner.snapshot()
            self.assertIn(wait_holder,scanner.top3_locked['bullish'])
            temporarily_waiting['value']=True
            # It still has a high *numerical* OB, but WAIT status is ineligible.
            # The best available ready leader can replace it after 3 revisions.
            for _ in range(3):
                scanner.revision+=1
                result=scanner.snapshot()
            self.assertNotIn(wait_holder,scanner.top3_locked['bullish'])
            self.assertIn(bull[3],scanner.top3_locked['bullish'])
            self.assertTrue(all(len(result[d+'Picks'])<=3 for d in ('bullish','bearish')))

    def test_equal_score_or_single_update_never_forces_replacement(self):
        from unittest.mock import patch
        scanner=Scanner(demo=True)
        sides,scores,get_ob=self._ready_data(scanner)
        bull=sides['bullish']
        with patch.object(scanner,'cached_leader_ob',side_effect=get_ob):
            scanner.snapshot()
            scores[bull[3]]=80  # Equal to current weakest, not higher
            for _ in range(5):
                scanner.revision+=1
                snap=scanner.snapshot()
            self.assertEqual(set(scanner.top3_locked['bullish']),set(bull[:3]))
            self.assertIsNone(snap['top3Pending']['bullish'])

    def test_wait_candidate_with_unconfirmed_zone_cannot_replace_ready(self):
        from unittest.mock import patch
        scanner=Scanner(demo=True)
        sides,scores,get_ob=self._ready_data(scanner)
        bull=sides['bullish']
        def conditional_ob(rec,status):
            ob=get_ob(rec,status)
            return ({**ob,'obState':'INVALID','obQualityScore':100}
                    if rec['symbol']==bull[4] else ob)
        with patch.object(scanner,'cached_leader_ob',side_effect=conditional_ob):
            scanner.snapshot()
            for _ in range(5):
                scanner.revision+=1
                snap=scanner.snapshot()
            self.assertNotIn(bull[4],[r['symbol'] for r in snap['bullishPicks']])

    def test_direction_flip_does_not_show_fake_confirmed_pick(self):
        scanner=Scanner(demo=True)
        first=scanner.snapshot()
        old=first['bullishPicks'][0]['symbol']
        scanner.records[old]['change']=-.55
        next_snap=scanner.snapshot()
        changed=next(r for r in next_snap['bullishPicks'] if r['symbol']==old)
        self.assertEqual(changed['pickStatus'],'SIDE CHANGED')
        self.assertTrue(changed['pickDirectionChanged'])
        self.assertFalse(changed['pickConfirmed'])

    def test_partial_ob_scores_fill_slots_without_invalid_substitutions(self):
        from unittest.mock import patch
        scanner=Scanner(demo=True)
        sides,scores,get_ob=self._ready_data(scanner)
        bull=sides['bullish']
        scores.update({symbol:None for symbol in bull})
        scores[bull[0]]=80
        with patch.object(scanner,'cached_leader_ob',side_effect=get_ob):
            partial=scanner.snapshot()
            self.assertEqual([r['symbol'] for r in partial['bullishPicks']],bull[:1])
            scores[bull[4]]=95
            scores[bull[3]]=90
            full=scanner.snapshot()
            self.assertEqual([r['symbol'] for r in full['bullishPicks']],[bull[4],bull[3],bull[0]])

    def test_session_rollover_releases_members_and_pending_challenges(self):
        scanner=Scanner(demo=True)
        scanner.snapshot()
        scanner.top3_locked['bullish']=['IMAGINARY']
        scanner.top3_pending['bullish']={'challenger':'ABC','incumbent':'XYZ','count':1}
        scanner.session_date='2099-10-30'
        next_snap=scanner.snapshot()
        self.assertNotIn('IMAGINARY',[r['symbol'] for r in next_snap['bullishPicks']])
        self.assertEqual(scanner.top3_lock_date,'2099-10-30')
        self.assertIsNone(scanner.top3_pending['bullish'])

    def test_warmup_does_not_substitute_random_blank_ob_scores(self):
        scanner = Scanner(demo=False)
        for i, symbol in enumerate(('PGEL','DIXON','INFY','KEI','SRF','TCS')):
            change = (1 if i < 3 else -1) * (2.5 - i*.15)
            scanner.ingest_quote({'symbol':symbol,'price':100*(1+change/100),
                                  'prev_close':100, 'volume':100000 + i*100,
                                  'timestamp':datetime.now(IST).isoformat()})
        snapshot = scanner.snapshot()
        self.assertEqual(snapshot['bullishPicks'], [])
        self.assertEqual(snapshot['bearishPicks'], [])
        self.assertFalse(any(snapshot['top3Locked'].values()))

    def test_real_last_twenty_minutes_outrank_opening_spike(self):
        scanner = Scanner(demo=False)
        end = datetime.now(IST).replace(second=0, microsecond=0)
        start = end - timedelta(minutes=20)
        symbols = ['PGEL','DIXON','BHEL','INFY','TCS','RELIANCE','SBIN','MARUTI']
        for sym in symbols:
            for i in range(21):
                p = (100 if i == 0 else 110) if sym == 'PGEL' else 100 + i*.19
                scanner.ingest_quote({'symbol':sym,'price':p,'prev_close':100,'volume':2000+i*300,
                                      'rvol':2.3,'timestamp':(start+timedelta(minutes=i)).isoformat()})
        snap = scanner.snapshot()
        self.assertNotEqual(snap['bullish'][0]['symbol'],'PGEL')
        self.assertNotIn('PGEL',[r['symbol'] for r in snap['bullish']])
        self.assertEqual(len(snap['bullish']),6)
        self.assertEqual(snap['bullishPicks'], [])  # No unscored candle patterns fabricated
        top = snap['bullish'][0]
        self.assertEqual(scanner.details(top['symbol'])['rank'],top['rank'])
        self.assertGreater(top['adaptiveRankScore'], adaptive_rank(next(
            scanner.public_record(x) for x in scanner.records.values() if x['symbol']=='PGEL')))

    def test_missing_direction_does_not_invent_symbols(self):
        scanner=Scanner(demo=False)
        for sym in ('PGEL','DIXON'):
            scanner.ingest_quote({'symbol':sym,'price':105,'prev_close':100,
                                  'timestamp':datetime.now(IST).isoformat()})
        snap=scanner.snapshot()
        self.assertEqual(len(snap['bullishPicks']),0)
        self.assertEqual(len(snap['bearishPicks']),0)

    def test_frontend_no_longer_discards_unconfirmed_candidates(self):
        from pathlib import Path
        js=(Path(__file__).parent.parent/'static/app.js').read_text()
        self.assertIn('selected=(state.snapshot?.[dir+\'Picks\']||[]).slice(0,3)',js)
        self.assertNotIn('rows.filter(r=>r.pickEligible)',js)
        self.assertIn('candidate-status', js)
        self.assertNotIn('selected=sortPicksByObScore(visibleStocks(dir)).slice(0,3)', js)
        self.assertIn('STABLE OB RANK', js)
        self.assertIn('WAITING FOR ELIGIBLE OB', js)


if __name__ == '__main__': unittest.main()
