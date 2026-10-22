"""Guard Top 6 score placement and auto-follow chart navigation with Node's JS VM."""
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parent

class ScoreHeaderTests(unittest.TestCase):
    def test_scores_replace_sequence_numbers_in_tile_header(self):
        source=(ROOT/'static/app.js').read_text()
        segment=source[source.index('function renderTile('):source.index('function renderLane(')]
        self.assertIn('leader-header-scores', segment)
        self.assertIn('leader-head-mom', segment)
        self.assertIn('leader-head-ob', segment)
        self.assertIn('momentumScore100', segment)
        self.assertIn('obQualityScore', segment)
        self.assertIn('obState', segment)
        self.assertNotIn('class="tile-rank"', segment)
        self.assertNotIn('leader-tile-scores', segment)
        css=(ROOT/'static/top6-scores.css').read_text()
        self.assertIn('.leader-header-scores', css)
        self.assertIn('html', (ROOT/'static/index.html').read_text())

    def test_chart_live_follow_default_and_manual_history(self):
        script = r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const js=fs.readFileSync('static/app.js','utf8');
const source=js.slice(js.indexOf('const chartClamp='),js.indexOf('function chartSVG('));
const context={console,Math,window:{matchMedia:()=>({matches:false})},
    state:{detail:{symbol:'PGEL',candles:[],timeframe:'5m'},drawer:'PGEL',chartView:null,
           timeframe:'5m',chartFollowLive:true}};
vm.createContext(context);
vm.runInContext(source+';renderChartOnly=()=>{};',context);
const state=context.state;
function push(n){for(let k=0;k<n;k++)state.detail.candles.push({close:500+k});}
push(80);
let v=context.chartWindow(state.detail);
assert.equal(v.start,10);assert.equal(v.end,80);assert.equal(state.chartFollowLive,true);
push(1);v=context.chartWindow(state.detail);
assert.equal(v.start,11);assert.equal(v.end,81);
assert.equal(context.chartZoom(.75),true);
assert.equal(state.chartFollowLive,true);
assert.equal(state.chartView.end,81);
const oldWidth=state.chartView.end-state.chartView.start;
push(1);v=context.chartWindow(state.detail);
assert.equal(v.end,82);assert.equal(v.start,82-oldWidth);
context.chartPan(-8);
assert.equal(state.chartFollowLive,false);
const frozenEnd=state.chartView.end;
push(1);v=context.chartWindow(state.detail);
assert.equal(v.end,frozenEnd);assert.equal(v.total,83);
assert.equal(context.chartWindowLabel(state.detail).includes('HISTORY VIEW'),true);
context.chartGoLive();assert.equal(state.chartFollowLive,true);
assert.equal(state.chartView.end,83);
assert.equal(context.chartWindowLabel(state.detail).includes('AUTO-FOLLOW'),true);
push(1);assert.equal(context.chartWindow(state.detail).end,84);
context.chartPan(-6);assert.equal(state.chartFollowLive,false);
context.chartFit();assert.equal(state.chartFollowLive,true);
assert.equal(state.chartView.start,0);
assert.equal(state.chartView.end,84);
console.log('Auto-follow, zoom, history persistence and LIVE resume tests passed');'''
        result=subprocess.run(['node','-e',script], cwd=str(ROOT), text=True,
                              capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0, result.stderr+result.stdout)

    def test_chart_refresh_keeps_auto_on_initial_navigation(self):
        source=(ROOT/'static/app.js').read_text()
        self.assertIn("chartFollowLive: true",source)
        self.assertIn("state.chartFollowLive=true;showDrawerLoading(symbol)",source)
        self.assertIn('state.chartFollowLive=true; // opening a chart',source)
        self.assertIn("'● AUTO'",source)
        self.assertIn('15000);',source)
        self.assertIn('chartGoLive()',source)

if __name__=='__main__':unittest.main()
