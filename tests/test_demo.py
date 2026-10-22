"""The public demo uses synthetic state and never calls the inverter API."""
import importlib.util
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_demo_matches_dashboard_and_blocks_network():
    spec = importlib.util.spec_from_file_location('build_demo', ROOT/'scripts/build-demo.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    html = (ROOT/'docs/index.html').read_text()
    assert module.build() == html
    assert "connect-src 'none'" in html
    assert "await fetch(" not in html
    assert 'Synthetic data' in html
    assert 'nasnas.local' not in html


def test_demo_renders_day_price_and_toggle_controls_without_api_calls():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for the dashboard JavaScript integration check')
    script = r'''
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const html=fs.readFileSync('docs/index.html','utf8');
const elements=new Map();
class Element {
 constructor(){this.children=[];this.style={};this.dataset={};this.attributes={};this.textContent='';this.classList={toggle(){},add(){},remove(){}};}
 append(...items){this.children.push(...items)}
 appendChild(item){this.append(item)}
 replaceChildren(...items){this.children=items}
 setAttribute(k,v){this.attributes[k]=v}
 getBoundingClientRect(){return {width:1100,height:280,left:0}}
 querySelector(){return this.child||(this.child=new Element())}
 getContext(){return new Proxy({}, {get:(obj,key)=>key in obj?obj[key]:(...args)=>{if(['moveTo','lineTo','fillRect'].includes(key)) assert(args.every(Number.isFinite));},set:(obj,key,value)=>(obj[key]=value,true)})}
}
const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
const days=['today','tomorrow','all'].map(day=>{const el=new Element();el.dataset.day=day;return el});
const context=vm.createContext({console,Intl,Date,AbortController,window:{devicePixelRatio:2,addEventListener(){}},setInterval(){},setTimeout,clearTimeout,
 document:{getElementById:get,createElement:()=>new Element(),querySelectorAll:()=>days},
 fetch(){throw Error('Unexpected network call')}});
vm.runInContext(html.split('<script>')[1].split('</script>')[0],context);
(async()=>{
 await vm.runInContext('refresh()',context);
 assert.equal(get('connectionError').textContent,'');
 assert.notEqual(get('soc').textContent,'—');
 assert(get('priceStats').textContent.includes('Mean'));
 days[1].onclick();
 assert(get('planHeading').textContent.includes('Tomorrow'));
 get('priceBasis').value='import';get('priceBasis').onchange();
 assert(get('priceLegend').textContent.includes('Your import'));
 days[0].onclick();
 get('outlook').onpointermove({clientX:46+40.1/96*(1100-89)});
 assert(get('tooltip').textContent.includes('Expected'));
 assert(get('tooltip').textContent.includes('Measured'));
 assert(get('tooltip').textContent.includes('Δ'));
 assert(get('socDeviation').textContent.includes('percentage points'));
 get('outlook').onpointerleave();
 assert.equal(get('tooltip').textContent,'');
 await get('toggle').onclick();
 assert.equal(vm.runInContext('data.control.enabled',context),false);
 await get('toggle').onclick();
 assert.equal(vm.runInContext('data.control.enabled',context),true);
 assert.equal(get('connectionError').textContent,'');
 console.log('Demo rendering, charts, day navigation, price basis and ON/OFF passed without network');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run([node, '-e', script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
