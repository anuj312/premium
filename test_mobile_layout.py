"""Static checks for the responsive client bundle without a JS build step."""
import unittest
from pathlib import Path

ROOT=Path(__file__).parent/'static'
class MobileLayoutTests(unittest.TestCase):
    def test_responsive_stylesheet_is_loaded_last(self):
        html=(ROOT/'index.html').read_text()
        self.assertIn('/static/mobile.css',html)
        self.assertGreater(html.index('/static/mobile.css'),html.index('/static/intelligence.css'))
    def test_touch_layout_has_phone_and_tablet_breakpoints(self):
        css=(ROOT/'mobile.css').read_text()
        self.assertIn('max-width:960px',css)
        self.assertIn('max-width:600px',css)
        self.assertIn('overflow-x:hidden!important',css)
        self.assertIn('overflow-x:auto',css)
    def test_stock_chart_remains_multi_timeframe(self):
        js=(ROOT/'app.js').read_text()
        self.assertIn('data-action="timeframe"', js)
        self.assertIn('data-start-index', js)
        self.assertIn('const mobileChart=', js)
    def test_mobile_does_not_embed_api_keys(self):
        css=(ROOT/'mobile.css').read_text()
        self.assertNotIn('KITE_ACCESS_TOKEN=',css)

if __name__=='__main__':unittest.main()
