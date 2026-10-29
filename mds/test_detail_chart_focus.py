"""Regression tests for large chart-first stock detail layout.
No browser data connectivity or new price/OI calculations are required.
"""
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parent.parent/'static'

class DetailChartFocusTests(unittest.TestCase):
    def test_compact_header_groups_stock_data_and_order_block(self):
        app=(ROOT/'app.js').read_text()
        segment=app[app.index('function renderDrawer()'):app.index('function indicatorButton(')]
        self.assertIn('class="detail-overview"',segment)
        self.assertIn('class="detail-ident"',segment)
        self.assertIn('class="detail-head-metrics"',segment)
        self.assertIn('FUTURES OI Δ',segment)
        self.assertIn('PRICE ACTION',segment)
        self.assertIn('RELATIVE VOLUME',segment)
        self.assertIn('OB QUALITY',segment)
        self.assertIn('d.obReason',segment)
        self.assertIn('d.obState',segment)
        self.assertLess(segment.index('class="detail-ob-metric"'),segment.index('class="detail-card chart-panel"'))
        self.assertLess(segment.index('class="detail-overview-controls"'),segment.index('class="detail-card chart-panel"'))
        self.assertNotIn('class="signal-strip"',segment)
        self.assertNotIn('class="ob-intel-bar"',segment)

    def test_working_timeframe_auto_tradingview_and_zoom_controls_preserved(self):
        app=(ROOT/'app.js').read_text()
        for token in ('data-action="timeframe"','data-action="chart-live"',
                      'data-action="chart-zoom-in"','data-action="chart-zoom-out"',
                      'data-action="chart-fit"',"tradingViewLink(d.symbol,'chart')",
                      'chartGoLive()','chartZoom(.75)','chartFit()'):
            self.assertIn(token,app)
        self.assertIn("mobileChart?'none':'xMidYMid meet'",app)
        self.assertEqual(subprocess.run(['node','--check',str(ROOT/'app.js')],capture_output=True).returncode,0)

    def test_responsive_style_loaded_after_older_chart_styles(self):
        html=(ROOT/'index.html').read_text()
        self.assertIn('detail-chart-focus.css?v=__ASSET_VERSION__',html)
        self.assertGreater(html.index('detail-chart-focus.css'),html.index('tradingview-links.css'))
        css=(ROOT/'detail-chart-focus.css').read_text()
        self.assertIn('grid-template-rows:auto minmax(0,1fr)',css)
        self.assertIn('@media(max-width:700px)',css)
        self.assertIn('@media(max-width:410px)',css)
        self.assertIn(':root[data-theme="dark"]',css)

if __name__=='__main__':unittest.main()
