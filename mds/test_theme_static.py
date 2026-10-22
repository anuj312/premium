"""Cheap theme regression checks that run without launching a browser or Kite."""
from pathlib import Path
import unittest

ROOT = Path(__file__).parent / 'static'

class TestThemeWiring(unittest.TestCase):
    def test_theme_loaded_after_responsive_css(self):
        content = (ROOT / 'index.html').read_text()
        self.assertLess(content.index('/static/mobile.css'), content.index('/static/theme.css'))
        self.assertIn('of-theme', content)

    def test_toggle_and_storage_persistence(self):
        js = (ROOT / 'app.js').read_text()
        self.assertIn('id="theme-toggle"', js)
        self.assertIn('data-action="toggle-theme"', js)
        self.assertIn("localStorage.setItem('of-theme'", js)
        self.assertIn("document.documentElement.dataset.theme", js)
        self.assertIn("Switch to light mode", js)
        self.assertIn("Switch to dark mode", js)

    def test_chart_dark_canvas_and_mobile_rule(self):
        css = (ROOT / 'theme.css').read_text()
        js = (ROOT / 'app.js').read_text()
        self.assertIn('--chart-bg:#101e28', css)
        self.assertIn('fill="var(--chart-bg,#fff)"', js)
        self.assertIn('@media(max-width:600px)', css)
        self.assertIn('.market-screen .header-right', css)

if __name__=='__main__': unittest.main()
