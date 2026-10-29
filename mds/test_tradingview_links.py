"""TradingView external links are five-minute NSE links and are independent of chart-opening buttons."""
from pathlib import Path
from fastapi.testclient import TestClient
import subprocess
import unittest
import server

ROOT=Path(__file__).parent.parent

class TradingViewLinksTests(unittest.TestCase):
    def test_app_html_has_asset_and_cache_bust(self):
        client=TestClient(server.app)
        page=client.get('/')
        assert page.status_code==200
        assert f'/static/tradingview-links.css?v={server.UI_ASSET_VERSION}' in page.text
        asset=client.get('/static/tradingview-links.css')
        assert asset.status_code==200
        assert 'no-store' in asset.headers['cache-control']
        assert client.get('/api/version').json()['tradingView5mLinks'] is True

    def test_real_symbol_link_and_markup(self):
        js=r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const js=fs.readFileSync('static/app.js','utf8');
const help=js.slice(js.indexOf('function tradingViewUrl('),js.indexOf('const istTime ='));
const render=js.slice(js.indexOf('function recentChange('),js.indexOf('function renderLane('));
const badge=js.slice(js.indexOf('function momentumScoreBadge('),js.indexOf('function renderPicks('));
const sandbox={Math,Number,Array,String,JSON,console,encodeURIComponent,esc:s=>String(s),
change:x=>String(x)+'%',fmt:x=>String(x),rvolText:x=>String(x),freshShort:()=>"ACTIVE",
state:{snapshot:{mode:'kite',marketStatus:'LIVE'}}};vm.createContext(sandbox);vm.runInContext(help+render+badge,sandbox);
assert.strictEqual(sandbox.tradingViewUrl('PGEL'),'https://www.tradingview.com/chart/?symbol=NSE%3APGEL&interval=5');
assert.strictEqual(sandbox.tradingViewUrl('M&M'),'https://www.tradingview.com/chart/?symbol=NSE%3AM%26M&interval=5');
assert.strictEqual(sandbox.tradingViewUrl('bad?ticker'),'');
const r={symbol:'PGEL',sector:'DURABLES',change:3.2,buildup:'LONG BUILD-UP',momentumScore100:79,
obQualityScore:84,obState:'WAIT',rvol:2.5,recent5mPct:.6,recent15mPct:1.0,candleProfile:{bars:[.5,1],poc:1,observations:2}};
for(const html of [sandbox.renderTile(r,'bullish'),sandbox.renderTile(r,'bearish'),sandbox.renderPick(r,0,'bullish'),sandbox.renderPick(r,0,'bearish')]){
  assert.match(html,/href="https:\/\/www.tradingview.com\/chart\/\?symbol=NSE%3APGEL(?:&amp;|&)interval=5"/);
  assert.match(html,/target="_blank" rel="noopener noreferrer"/);
  assert.match(html,/data-tradingview="PGEL"/);
  assert.match(html,/data-stock="PGEL"/);
  assert.ok(html.indexOf('</button>') < html.indexOf('class="tv-open-link'), 'TV anchor is outside internal chart-opening button');
}
console.log('Verified independent TradingView shortcuts and NSE 5m URLs');'''
        result=subprocess.run(['node','-e',js],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)

if __name__=='__main__': unittest.main()
