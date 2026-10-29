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
        # This test overrides the demo scores; clear the already-formed demo lock.
        scanner.top3_locked = {'bullish': [], 'bearish': []}
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


    def test_pinned_three_never_replace_when_top6_and_ob_scores_change(self):
        from unittest.mock import patch
        scanner = Scanner(demo=True)
        first = scanner.snapshot()
        locked = {direction: [r['symbol'] for r in first[direction+'Picks']]
                  for direction in ('bullish', 'bearish')}
        self.assertTrue(all(len(rows)==3 for rows in locked.values()))
        self.assertTrue(all(first['top3Locked'].values()))
        # Another stock now becomes the strongest; a pinned name also drops
        # out of the Top 6 and loses its OB pattern completely.
        for direction in ('bullish', 'bearish'):
            old = locked[direction][0]
            scanner.records[old]['change'] = .01 if direction=='bullish' else -.01
            scanner.records[old]['price'] = scanner.records[old]['prevClose']*(1 + scanner.records[old]['change']/100)
        with patch.object(scanner, 'cached_leader_ob', return_value={
            'obQualityScore': None, 'obState': 'WAIT', 'obReason': 'Pattern disappeared'}):
            second = scanner.snapshot()
        for direction in ('bullish','bearish'):
            self.assertEqual(set(r['symbol'] for r in second[direction+'Picks']), set(locked[direction]))
            self.assertEqual(len(second[direction+'Picks']),3)
            self.assertTrue(all(r['pickPinned'] for r in second[direction+'Picks']))
            # The current score may become absent, but its stock is not replaced.
            self.assertTrue(any(row['obQualityScore'] is None
                                for row in second[direction+'Picks']))
            self.assertTrue(all('candleProfile' in r for r in second[direction+'Picks']))

    def test_locked_bullish_and_bearish_reorder_by_current_ob_without_replacing_stocks(self):
        from unittest.mock import patch
        scanner = Scanner(demo=True)
        scanner.snapshot()  # Establish the initial lock with three stocks per side.
        pinned = {direction: list(scanner.top3_locked[direction])
                  for direction in ('bullish', 'bearish')}
        self.assertTrue(all(len(names) == 3 for names in pinned.values()))
        scores = {}
        for names in pinned.values():
            scores.update(zip(names, (70, 80, 82)))

        def current_ob(record, market_status):
            # Unselected leaders have higher OB scores but must NOT enter.
            return {'obQualityScore': scores.get(record['symbol'], 99), 'obState': 'WAIT'}

        with patch.object(scanner, 'cached_leader_ob', side_effect=current_ob):
            first = scanner.snapshot()
            for direction, names in pinned.items():
                picks = first[direction + 'Picks']
                self.assertEqual([r['symbol'] for r in picks], names[::-1])
                self.assertEqual([r['obQualityScore'] for r in picks], [82, 80, 70])
                self.assertEqual(scanner.top3_locked[direction], names)
            # Change OB scores: the same three stocks move to their new order.
            for names in pinned.values():
                scores[names[0]] = 95
                scores[names[1]] = 70
                scores[names[2]] = None
            next_snapshot = scanner.snapshot()
            for direction, names in pinned.items():
                picks = next_snapshot[direction + 'Picks']
                self.assertEqual([r['symbol'] for r in picks], names)
                self.assertEqual([r['obQualityScore'] for r in picks], [95, 70, None])
                self.assertEqual(scanner.top3_locked[direction], names)

    def test_direction_flip_keeps_pinned_ticker_but_cannot_confirm_it(self):
        scanner=Scanner(demo=True)
        first=scanner.snapshot()
        old=first['bullishPicks'][0]['symbol']
        scanner.records[old]['change']=-0.55
        next_snap=scanner.snapshot()
        changed=next(r for r in next_snap['bullishPicks'] if r['symbol']==old)
        self.assertEqual(changed['pickStatus'], 'SIDE CHANGED')
        self.assertTrue(changed['pickDirectionChanged'])
        self.assertFalse(changed['pickConfirmed'])


    def test_partial_ob_scores_pin_progressively_without_substitution(self):
        from unittest.mock import patch
        scanner=Scanner(demo=True)
        first=scanner.snapshot()
        scanner.top3_locked={'bullish':[],'bearish':[]}
        first_bull=[r['symbol'] for r in first['bullish']]
        scores={symbol: None for symbol in first_bull}
        scores[first_bull[0]]=60
        with patch.object(scanner,'cached_leader_ob',side_effect=lambda rec,status:{
                 'obQualityScore':scores.get(rec['symbol']), 'obState':'WAIT'}):
            partial=scanner.snapshot()
            self.assertEqual([r['symbol'] for r in partial['bullishPicks']],first_bull[:1])
            self.assertTrue(partial['top3Locked']['bullish'])
            scores[first_bull[0]]=None  # The previous score vanished
            scores[first_bull[3]]=99    # A higher score enters, but old stays
            scores[first_bull[4]]=82
            full=scanner.snapshot()
        self.assertEqual([r['symbol'] for r in full['bullishPicks']],
                         [first_bull[3],first_bull[4],first_bull[0]])
        self.assertIsNone(full['bullishPicks'][-1]['obQualityScore'])
        self.assertTrue(all(r['pickPinned'] for r in full['bullishPicks']))

    def test_session_rollover_releases_pinned_three(self):
        scanner=Scanner(demo=True)
        first=scanner.snapshot()
        self.assertTrue(first['top3Locked']['bullish'])
        scanner.session_date='2099-10-30'
        scanner.top3_locked['bullish']=['IMAGINARY']
        next_snap=scanner.snapshot()
        self.assertNotIn('IMAGINARY', [r['symbol'] for r in next_snap['bullishPicks']])
        self.assertEqual(scanner.top3_lock_date,'2099-10-30')

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
        self.assertIn('SESSION LOCKED', js)
        self.assertIn('no unscored stocks substituted', js)


if __name__ == '__main__': unittest.main()
