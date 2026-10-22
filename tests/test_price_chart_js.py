"""Exercise the real canvas renderer: morning visibility, spread and linearity."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_price_renderer_keeps_full_day_and_draws_linear_uncompressed_spread():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the actual dashboard renderer")
    source = Path("src/manager/dashboard.html").read_text()
    renderer = source.split("function visiblePrices()", 1)[1].split("function charts()", 1)[0]
    script = r"""
const assert=require('node:assert/strict');
let data={now:'2026-10-21T10:00:00+00:00'},plan=[],selectedDay='all',priceBasis='spot';
const dayKey=t=>new Date(new Date(t).getTime()+7200000).toISOString().slice(0,10);
const targetDays=()=>({today:'2026-10-21',tomorrow:'2026-10-22'});
const clock=t=>new Date(t).toISOString().slice(11,16),dayTitle=x=>x;
const local=t=>new Date(t).toISOString();
const minuteOfDay=t=>(new Date(t).getUTCHours()+2)%24*60+new Date(t).getUTCMinutes();
const fmt=(n,d=1)=>Number.isFinite(n)?n.toFixed(d):'—';
const captions={},paths=[];let points=[];
const c={scale(){},fillText(){},fillRect(){},save(){},restore(){},setLineDash(){},
beginPath(){points=[]},moveTo(x,y){points.push([x,y])},lineTo(x,y){points.push([x,y])},
stroke(){paths.push({color:this.strokeStyle,points:[...points]})}};
const canvas={getBoundingClientRect:()=>({width:700,height:280}),getContext:()=>c};
const $=()=>canvas,text=(id,value)=>{captions[id]=value};const window={devicePixelRatio:1};
data.price_intervals=Array.from({length:96},(_,i)=>({
timestamp:new Date(Date.UTC(2026,9,20,22)+i*900000).toISOString(),
spot_eur_mwh:i===32?300:i===4?200:100,import_eur_kwh:i===32?.4:.25}));
plan=data.price_intervals.slice(49).map(r=>({...r,soc_percent:50}));
""" + "function visiblePrices()" + renderer + r"""
assert.equal(visiblePrices().length,96,'Morning prices must not disappear at noon');
assert.equal(visiblePrices()[32].spot_eur_mwh,300);
assert.equal(visiblePrices()[0].soc_percent,null,'Do not invent past SOC projections');
pricePlot();
const price=paths.find(p=>p.color==='#bd612c').points;
const slotWidth=(700-46-43)/96;
for(let i=0;i<96;i++){
assert.ok(Math.abs(price[2*i][0]-(46+i*slotWidth))<1e-8,'Price interval must start at its timestamp');
assert.ok(Math.abs(price[2*i+1][0]-(46+(i+1)*slotWidth))<1e-8,'Price must hold until the next quarter boundary');
assert.equal(price[2*i][1],price[2*i+1][1],'Do not interpolate prices across an interval');
}
const y100=price[0][1],y200=price[8][1],y300=price[64][1];
assert.ok(y100-y300>(280-46-32)*.8,'Price range must occupy the chart instead of an unused zero baseline');
assert.ok(Math.abs(2*y200-y100-y300)<1e-8,'Prices must map linearly, not logarithmically');
const soc=paths.find(p=>p.color==='#41674b').points;
assert.ok(soc[0][0]>46+(700-46-43)/2,'SOC projection must begin in the future');
assert.ok(captions.priceRange.includes('200.00 €/MWh'));
const negative=priceAxis([-30,20]);assert.ok(negative.min<-30&&negative.max>20);
const flat=priceAxis([100,100]);assert.ok(flat.max>flat.min);
priceBasis='import';pricePlot();assert.equal(captions.priceLegend,'Your import · €/MWh');
selectedDay='tomorrow';assert.equal(visiblePrices().length,0);
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
