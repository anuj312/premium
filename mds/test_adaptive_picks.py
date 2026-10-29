"""Regression tests: visible Top 3 + no all-day opening-spike lock-in."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from server import Scanner
from adaptive_ranking import adaptive_rank, sort_picks_by_ob_score

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

    def test_snapshot_top3_ob_quality_first_for_both_sides(self):
        from unittest.mock import patch
        scanner = Scanner(demo=True)
        original_snapshot = scanner.snapshot()
        momentum = {}
        ob_scores = {}
        for direction in ('bullish', 'bearish'):
            six = original_snapshot[direction]
            self.assertEqual(len(six), 6)
            # OB winners (indices 3, 4, 1) differ from MOM winners (0, 1, 2).
            for i, row in enumerate(six):
                momentum[row['symbol']] = 100 - i * 10
                ob_scores[row['symbol']] = [15, 90, 40, 100, 100, None][i]
        original_record = scanner.public_record

        def with_momentum(rec):
            row = original_record(rec)
            if row['symbol'] in momentum:
                row['momentumScore100'] = momentum[row['symbol']]
            return row

        def with_ob(rec, status):
            return {'obQualityScore': ob_scores.get(rec['symbol']), 'obState': 'WAIT'}

        with patch.object(scanner, 'public_record', side_effect=with_momentum), \
             patch.object(scanner, 'cached_leader_ob', side_effect=with_ob):
            snap = scanner.snapshot()
        for direction in ('bullish', 'bearish'):
            six = original_snapshot[direction]
            expected = [six[i]['symbol'] for i in (3, 4, 1)]
            picks = snap[direction + 'Picks']
            self.assertEqual([r['symbol'] for r in picks], expected)
            self.assertEqual([r['obQualityScore'] for r in picks], [100, 100, 90])
            self.assertTrue(all(r['pickStatus'] == 'DEMO' for r in picks))
            self.assertTrue(all('candleProfile' in r for r in picks))

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
        js=(Path(__file__).parent.parent/'static/app.js').read_text()
        self.assertIn('selected=(state.snapshot?.[dir+\'Picks\']||[]).slice(0,3)',js)
        self.assertNotIn('rows.filter(r=>r.pickEligible)',js)
        self.assertIn('candidate-status', js)
        self.assertIn('selected=sortPicksByObScore(visibleStocks(dir)).slice(0,3)', js)
        self.assertIn('OB SCORE ↓', js)
        self.assertIn('Top three OB Quality scores', js)


if __name__ == '__main__': unittest.main()
