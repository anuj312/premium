"""Static regression guard for Top 6 headers and three-row leader tiles."""
import unittest
from pathlib import Path

ROOT = Path(__file__).parent

class TopSixFrontendTests(unittest.TestCase):
    def test_labels_and_counter_are_six(self):
        js = (ROOT / 'static/app.js').read_text()
        self.assertIn('TOP 6', js)
        self.assertIn('Top 6 → Top 3', js)
        self.assertIn('/ 6</span>', js)
        self.assertNotIn('TOP 10', js)

    def test_css_three_rows_and_two_columns(self):
        terminal = (ROOT / 'static/reference-terminal.css').read_text()
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))', terminal)
        self.assertIn('grid-template-rows:repeat(3,minmax(0,1fr))', terminal)
        self.assertIn('grid-template-rows:repeat(3,minmax(88px,auto))', terminal)
        self.assertIn('grid-template-rows:repeat(3,minmax(84px,auto))', terminal)

if __name__ == '__main__':
    unittest.main()
