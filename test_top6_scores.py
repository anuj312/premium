"""Regression checks: Top 6 must display the very same score data as Top 3."""
import unittest
from pathlib import Path
from unittest.mock import patch
from server import Scanner

ROOT=Path(__file__).parent

class TopSixScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scanner=Scanner(demo=True)

    def test_all_twelve_leaders_include_real_score_fields(self):
        snapshot=self.scanner.snapshot()
        for direction in ('bullish','bearish'):
            self.assertEqual(len(snapshot[direction]),6)
            for row in snapshot[direction]:
                self.assertIn('momentumScore100',row)
                self.assertIn('momentumScoreBasis',row)
                self.assertIn('obQualityScore',row)
                self.assertIn('obState',row)
                self.assertIn('obReason',row)
                self.assertIn(row['obState'],('WAIT','READY','INVALID'))
                self.assertNotIn('trueDelta',row)

    def test_shared_picks_have_same_scores_and_ob_status(self):
        state=self.scanner.snapshot()
        for direction in ('bullish','bearish'):
            leaders={r['symbol']:r for r in state[direction]}
            for pick in state[direction+'Picks']:
                orig=leaders[pick['symbol']]
                self.assertEqual(pick['momentumScore100'],orig['momentumScore100'])
                self.assertEqual(pick['obQualityScore'],orig['obQualityScore'])
                self.assertEqual(pick['obState'],orig['obState'])

    def test_ob_signal_cache_reduces_reanalysis_on_sse_refresh(self):
        scanner=Scanner(demo=True)
        with patch.object(scanner,'order_block_signal',wraps=scanner.order_block_signal) as calc:
            scanner.snapshot()
            first_count=calc.call_count
            self.assertEqual(first_count,12)
            scanner.snapshot()
            self.assertEqual(calc.call_count,first_count)

    def test_ui_reuses_same_badge_components_as_top_three(self):
        js=(ROOT/'static/app.js').read_text()
        css=(ROOT/'static/top6-scores.css').read_text()
        html=(ROOT/'static/index.html').read_text()
        self.assertIn('leader-header-scores',js)
        self.assertIn('leader-head-mom',js)
        self.assertIn('leader-head-ob',js)
        self.assertNotIn('class="tile-rank"',js)
        self.assertIn('leader-momentum-state',js)
        self.assertIn('.leader-scored-card',css)
        self.assertIn('top6-scores.css',html)
        self.assertIn('renderPick(',js)
        self.assertIn('data-stock=',js)

if __name__=='__main__':unittest.main()
