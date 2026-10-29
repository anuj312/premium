"""Deployment regression: visible Top 6 cards and cache invalidation must ship together."""
from pathlib import Path
import subprocess
import unittest
from fastapi.testclient import TestClient
import server

ROOT = Path(__file__).parent.parent


class Top6DeployVersionTests(unittest.TestCase):
    def test_html_has_fingerprinted_assets_and_is_not_cached(self):
        client = TestClient(server.app)
        page = client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('no-store', page.headers['cache-control'])
        self.assertIn(f'/static/app.js?v={server.UI_ASSET_VERSION}', page.text)
        self.assertIn(f'/static/top6-scores.css?v={server.UI_ASSET_VERSION}', page.text)
        self.assertIn(f'<meta name="orderflow-ui-build" content="{server.UI_ASSET_VERSION}"', page.text)
        for asset in ('app.js', 'top6-scores.css'):
            response = client.get(f'/static/{asset}')
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response.headers['cache-control'])
        version = client.get('/api/version').json()
        self.assertEqual(version['uiBuild'], server.UI_ASSET_VERSION)
        self.assertEqual(version['top6CardHeader'], 'MOM + OB')
        self.assertFalse(version['legacyRankBadges'])

    def test_rendered_leader_card_has_scores_not_sequence_number(self):
        js_test = r'''const fs=require('fs'), vm=require('vm'), assert=require('assert');
const src=fs.readFileSync('static/app.js','utf8');
const code=src.slice(src.indexOf('function recentChange('),src.indexOf('function renderLane('));
const sandbox={Math,Array,Number,String,JSON,console,
  esc:s=>String(s??''), change:v=>`${v>=0?'+':''}${v}%`,
  fmt:v=>String(v), rvolText:v=>String(v??'N/A'), freshShort:r=>'ACTIVE',
  state:{snapshot:{mode:'kite'}}, tradingViewLink:s=>`<a class=\"tv-open-link\" href=\"https://www.tradingview.com/chart/?symbol=NSE%3A${s}&interval=5\"></a>`};
vm.createContext(sandbox);vm.runInContext(code,sandbox);
const row={symbol:'PGEL', sector:'DURABLES', change:4.1, buildup:'LONG BUILD-UP',
  momentumScore100:79, obQualityScore:84, obState:'WAIT', rvol:2.9,
  momentumState:'ACTIVE', recent5mPct:0.5, recent15mPct:1.1,
  candleProfile:{bars:[.2,.7,1],poc:2,observations:11}};
for(const direction of ['bullish','bearish']){
  let markup=sandbox.renderTile(row,direction);
  assert.match(markup,/leader-header-scores/);
  assert.match(markup,/MOM<\/small><b>79<\/b>/);
  assert.match(markup,/OB<\/small><b>84<\/b>/);
  assert.doesNotMatch(markup,/class="tile-rank"|>0[1-6]<\/span>/);
}
console.log('Verified Top 6 card markup: MOM 79 and OB 84; no rank label');'''
        result = subprocess.run(['node', '-e', js_test], cwd=ROOT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == '__main__':
    unittest.main()
