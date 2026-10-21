"""Tests for the reference-terminal view's truthful, low-cost candle profiles."""
import unittest

from server import Scanner, ALL_SYMBOLS


class ReferenceTerminalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = Scanner(demo=True)

    def test_top_twenty_have_profiles(self):
        state = self.market.snapshot()
        rows = state['bullish'] + state['bearish']
        self.assertEqual(len(rows), 20)
        for row in rows:
            profile = row['candleProfile']
            self.assertEqual(profile['source'], 'ESTIMATED_5M_OHLCV')
            self.assertEqual(len(profile['bars']), 17)
            self.assertTrue(0 <= profile['poc'] < 17)

    def test_picks_and_details_have_real_candle_profiles(self):
        state = self.market.snapshot()
        for side in ('bullishPicks', 'bearishPicks'):
            for row in state[side]:
                self.assertIn('candleProfile', row)
                self.assertIn('momentumScore100', row)
                self.assertIn('obQualityScore', row)
        detail = self.market.details('PGEL', '15m')
        self.assertIn('candleProfile', detail)
        self.assertEqual(detail['candleProfile']['source'], 'ESTIMATED_5M_OHLCV')
        self.assertEqual(detail['timeframe'], '15m')

    def test_nonleader_records_not_profiled_in_snapshot(self):
        state = self.market.snapshot()
        visible = {row['symbol'] for key in ('bullish', 'bearish') for row in state[key]}
        self.assertLess(len(visible), len(ALL_SYMBOLS))
        # The expensive distribution is computed on demand for just the visible
        # leaders / opened chart, never attached across every universe record.
        private_symbol = next(symbol for symbol in ALL_SYMBOLS if symbol not in visible)
        self.assertNotIn('candleProfile', self.market.public_record(self.market.records[private_symbol]))


if __name__ == '__main__':
    unittest.main()
