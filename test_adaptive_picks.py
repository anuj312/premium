"""Regression tests: visible Top 3 + no all-day opening-spike lock-in."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from server import Scanner
from adaptive_ranking import adaptive_rank, diversified_candidates

IST = ZoneInfo('Asia/Kolkata')


class AdaptivePickTests(unittest.TestCase):
    def test_sideways_early_spike_loses_rank_to_fresh_mover(self):
        old = {'change': 9, 'sessionMovePct': 9, 'momentumState': 'CONSOLIDATING',
               'momentumScore100': 38, 'volumeConfirmed': True}
        fresh = {'change': 1.4, 'sessionMovePct': 1.4, 'momentumState': 'ACTIVE',
                 'momentumScore100': 68, 'volumeConfirmed': True}
        self.assertGreater(adaptive_rank(fresh), adaptive_rank(old))
        self.assertLessEqual(adaptive_rank(old), 46)  # Opening spike capped

    def test_confirmed_same_sector_precedes_unconfirmed_other_sector(self):
        rows = [dict(symbol='A', sector='AUTO', pickEligible=True, adaptiveRankScore=80, score=90),
                dict(symbol='B', sector='AUTO', pickEligible=True, adaptiveRankScore=75, score=85),
                dict(symbol='C', sector='IT', pickEligible=False, adaptiveRankScore=30, score=110),
                dict(symbol='D', sector='ENERGY', pickEligible=False, adaptiveRankScore=22, score=80)]
        self.assertEqual([r['symbol'] for r in diversified_candidates(rows)], ['A','B','C'])

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


if __name__ == '__main__': unittest.main()
