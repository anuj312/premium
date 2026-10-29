"""Regression tests: visible Top 3 + no all-day opening-spike lock-in."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from server import Scanner
from adaptive_ranking import adaptive_rank, top_momentum_candidates, sort_picks_by_ob_score

IST = ZoneInfo('Asia/Kolkata')


class AdaptivePickTests(unittest.TestCase):
    def test_sideways_early_spike_loses_rank_to_fresh_mover(self):
        old = {'change': 9, 'sessionMovePct': 9, 'momentumState': 'CONSOLIDATING',
               'momentumScore100': 38, 'volumeConfirmed': True}
        fresh = {'change': 1.4, 'sessionMovePct': 1.4, 'momentumState': 'ACTIVE',
                 'momentumScore100': 68, 'volumeConfirmed': True}
        self.assertGreater(adaptive_rank(fresh), adaptive_rank(old))
        self.assertLessEqual(adaptive_rank(old), 46)  # Opening spike capped

    def test_top_three_momentum_ignores_sector_and_confirmation(self):
        rows = [dict(symbol='A', sector='AUTO', pickEligible=False, momentumScore100=91,
                     adaptiveRankScore=30, score=10),
                dict(symbol='B', sector='AUTO', pickEligible=False, momentumScore100=87,
                     adaptiveRankScore=25, score=20),
                dict(symbol='C', sector='IT', pickEligible=True, momentumScore100=65,
                     adaptiveRankScore=100, score=30),
                dict(symbol='D', sector='ENERGY', pickEligible=True, momentumScore100=60,
                     adaptiveRankScore=110, score=40)]
        self.assertEqual([r['symbol'] for r in top_momentum_candidates(rows)], ['A', 'B', 'C'])

    def test_missing_momentum_is_last_and_ties_use_adaptive_score(self):
        rows = [dict(symbol='STALE', momentumScore100=None, adaptiveRankScore=99),
                dict(symbol='B', momentumScore100=55, adaptiveRankScore=40),
                dict(symbol='A', momentumScore100=55, adaptiveRankScore=80),
                dict(symbol='C', momentumScore100=10, adaptiveRankScore=40)]
        self.assertEqual([r['symbol'] for r in top_momentum_candidates(rows)], ['A', 'B', 'C'])

    def test_ob_score_orders_chosen_stocks_not_selection(self):
        chosen = [dict(symbol='A', momentumScore100=91, obQualityScore=25),
                  dict(symbol='B', momentumScore100=87, obQualityScore=85),
                  dict(symbol='C', momentumScore100=65, obQualityScore=None)]
        self.assertEqual([r['symbol'] for r in sort_picks_by_ob_score(chosen)], ['B', 'A', 'C'])
        self.assertEqual([r['symbol'] for r in chosen], ['A', 'B', 'C'])

    def test_snapshot_uses_momentum_first_and_ob_second_for_both_sides(self):
        from unittest.mock import patch
        scanner = Scanner(demo=True)
        original_snapshot = scanner.snapshot()
        targets = {}
        ob_scores = {}
        for direction in ('bullish', 'bearish'):
            six = original_snapshot[direction]
            self.assertEqual(len(six), 6)
            # The first three by MOM differ from the first three by OB.
            for i, row in enumerate(six):
                targets[row['symbol']] = 100 - i * 10
                ob_scores[row['symbol']] = [15, 90, 40, 100, 100, 100][i]
        original_record = scanner.public_record

        def with_momentum(rec):
            row = original_record(rec)
            if row['symbol'] in targets:
                row['momentumScore100'] = targets[row['symbol']]
            return row

        def with_ob(rec, status):
            return {'obQualityScore': ob_scores.get(rec['symbol']), 'obState': 'WAIT'}

        with patch.object(scanner, 'public_record', side_effect=with_momentum), \
             patch.object(scanner, 'cached_leader_ob', side_effect=with_ob):
            snap = scanner.snapshot()
        for direction in ('bullish', 'bearish'):
            first_three = [r['symbol'] for r in original_snapshot[direction][:3]]
            self.assertEqual([r['symbol'] for r in snap[direction + 'Picks']],
                             [first_three[1], first_three[2], first_three[0]])
            self.assertTrue(all(r['pickStatus'] == 'DEMO' for r in snap[direction + 'Picks']))

    def test_warmup_does_not_leave_two_sides_empty(self):
        scanner = Scanner(demo=False)
        for i, symbol in enumerate(('PGEL','DIXON','INFY','KEI','SRF','TCS')):
            change = (1 if i < 3 else -1) * (2.5 - i*.15)
            scanner.ingest_quote({'symbol':symbol,'price':100*(1+change/100),
                                  'prev_close':100, 'volume':100000 + i*100,
                                  'timestamp':datetime.now(IST).isoformat()})
        snapshot = scanner.snapshot()
        self.assertEqual(len(snapshot['bullishPicks']),3)
        self.assertEqual(len(snapshot['bearishPicks']),3)
        self.assertTrue(all(not r['pickConfirmed'] for r in snapshot['bullishPicks']+snapshot['bearishPicks']))
        self.assertTrue(all(r['pickStatus'] != 'ACTIVE' for r in snapshot['bullishPicks']+snapshot['bearishPicks']))
        self.assertTrue(all('candleProfile' in r for r in snapshot['bullishPicks']+snapshot['bearishPicks']))

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
        self.assertEqual(len(snap['bullishPicks']),3)
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
        self.assertEqual(len(snap['bullishPicks']),2)
        self.assertEqual(len(snap['bearishPicks']),0)

    def test_frontend_no_longer_discards_unconfirmed_candidates(self):
        from pathlib import Path
        js=(Path(__file__).parent/'static/app.js').read_text()
        self.assertIn('selected=(state.snapshot?.[dir+\'Picks\']||[]).slice(0,3)',js)
        self.assertNotIn('rows.filter(r=>r.pickEligible)',js)
        self.assertIn('candidate-status', js)
        self.assertIn('selected=sortPicksByObScore(ranked.slice(0,3))', js)
        self.assertIn('OB SCORE ↓', js)


if __name__ == '__main__': unittest.main()
