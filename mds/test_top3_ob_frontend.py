"""Top 3 OB Quality selection works for the unfiltered and filtered UI."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent

class Top3OBFrontendTests(unittest.TestCase):
    def test_frontend_filtered_selection_uses_ob_quality_before_slicing(self):
        js = r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('static/app.js','utf8');
const code=src.slice(src.indexOf('function sortPicksByObScore('),src.indexOf('function renderData('));
const records={
 bullish:[{symbol:'MOM',sector:'TECH',momentumScore100:99,obQualityScore:10},
          {symbol:'BEST',sector:'TECH',momentumScore100:20,obQualityScore:95},
          {symbol:'NEXT',sector:'TECH',momentumScore100:45,obQualityScore:90},
          {symbol:'THIRD',sector:'TECH',momentumScore100:40,obQualityScore:80},
          {symbol:'OTHER',sector:'BANK',momentumScore100:80,obQualityScore:85},
          {symbol:'NONE',sector:'TECH',momentumScore100:99,obQualityScore:null}],
 bearish:[{symbol:'LOW',sector:'TECH',momentumScore100:100,obQualityScore:20},
          {symbol:'HIGHEST',sector:'TECH',momentumScore100:10,obQualityScore:99},
          {symbol:'MID',sector:'TECH',momentumScore100:40,obQualityScore:70},
          {symbol:'NEXTB',sector:'TECH',momentumScore100:30,obQualityScore:65}]
};
const element={innerHTML:''};
const state={selection:'TECH',watchOnly:false, snapshot:{...records,bullishPicks:[],bearishPicks:[]}};
const sandbox={state,Number,Array,Math, console,
 visibleStocks:dir=>state.snapshot[dir].filter(r=>r.sector===state.selection),
 renderPick:r=>`<pick>${r.symbol}</pick>`,
 $:selector=>element};
vm.createContext(sandbox);vm.runInContext(code,sandbox);
sandbox.renderPicks();
const html=element.innerHTML;
assert.match(html,/<pick>BEST<\/pick><pick>NEXT<\/pick><pick>THIRD<\/pick>/);
assert.match(html,/<pick>HIGHEST<\/pick><pick>MID<\/pick><pick>NEXTB<\/pick>/);
assert.doesNotMatch(html,/<pick>MOM<\/pick>|<pick>NONE<\/pick>/);
state.selection='ALL';
state.snapshot.bullishPicks=[{symbol:'PRE_SORTED',obQualityScore:91}];
state.snapshot.bearishPicks=[{symbol:'BEAR_PRE_SORTED',obQualityScore:89}];
sandbox.renderPicks();
assert.match(element.innerHTML,/<pick>PRE_SORTED<\/pick>/);
assert.match(element.innerHTML,/<pick>BEAR_PRE_SORTED<\/pick>/);
console.log('Verified independent OB Quality Top 3 with filters and unfiltered views');'''
        result=subprocess.run(['node','-e',js],cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)

if __name__=='__main__': unittest.main()
