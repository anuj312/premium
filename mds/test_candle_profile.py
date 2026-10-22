import unittest
from candle_profile import candle_volume_profile

class CandleProfileTest(unittest.TestCase):
    def test_empty_never_fakes_volume(self):
        self.assertEqual(candle_volume_profile([])['bars'], [])
        self.assertEqual(candle_volume_profile([{'high':10,'low':9,'volume':0}])['poc'], None)

    def test_volume_calculation_and_poc(self):
        candles=[{'low':100,'high':101,'volume':2000} for _ in range(6)] + [{'low':105,'high':107,'volume':200}]
        result=candle_volume_profile(candles, bins=15)
        self.assertEqual(len(result['bars']),15)
        self.assertGreater(max(result['bars']),0.99)
        self.assertLess(result['pocPrice'],103)
        self.assertEqual(result['source'],'ESTIMATED_5M_OHLCV')
        self.assertEqual(result['observations'],7)

    def test_bad_candles_not_used(self):
        profile=candle_volume_profile([{'low':10,'high':8,'volume':100},{'low':20,'high':22,'volume':10}])
        self.assertEqual(profile['observations'],1)

if __name__=='__main__':unittest.main()
