"""Top 3 OB Quality selection works for the unfiltered and filtered UI."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent

class Top3OBFrontendTests(unittest.TestCase):
    def test_top3_ignores_changing_sector_filters_and_locks_side_panel(self):
        js = r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('static/app.js','utf8');
const code=src.slice(src.indexOf('function renderPicks('),src.indexOf('function renderData('));
const element={innerHTML:''};
const state={selection:'TECH',watchOnly:true, snapshot:{
 bullishPicks:[{symbol:'BULL1',obQualityScore:97},{symbol:'BULL2',obQualityScore:88},{symbol:'BULL3',obQualityScore:null}],
 bearishPicks:[{symbol:'BEAR1',obQualityScore:98},{symbol:'BEAR2',obQualityScore:90},{symbol:'BEAR3',obQualityScore:80}],
 top3Locked:{bullish:true,bearish:true}}};
const sandbox={state,Number,Array,Math,console,
 renderPick:r=>`<pick>${r.symbol}</pick>`, $:selector=>element};
vm.createContext(sandbox);vm.runInContext(code,sandbox);
sandbox.renderPicks();
assert.match(element.innerHTML,/<pick>BULL1<\/pick><pick>BULL2<\/pick><pick>BULL3<\/pick>/);
assert.match(element.innerHTML,/<pick>BEAR1<\/pick><pick>BEAR2<\/pick><pick>BEAR3<\/pick>/);
assert.match(element.innerHTML,/SESSION LOCKED/);
state.selection='FINANCE';state.watchOnly=false;
sandbox.renderPicks();
assert.match(element.innerHTML,/<pick>BULL1<\/pick><pick>BULL2<\/pick><pick>BULL3<\/pick>/);
state.snapshot.bullishPicks=[{symbol:'BULL1',obQualityScore:97}];
state.snapshot.top3Locked.bullish=false;
sandbox.renderPicks();
assert.match(element.innerHTML,/2 slots waiting for valid OB Quality scores/);
assert.doesNotMatch(element.innerHTML,/<pick>OTHER<\/pick>/);
console.log('Verified pinned side panels independent of filters');'''
        result=subprocess.run(['node','-e',js],cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)

if __name__=='__main__': unittest.main()
