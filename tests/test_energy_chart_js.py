"""The real renderer leaves the rest of the day empty instead of stretching data."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_measured_energy_uses_full_day_time_axis():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for canvas checks")
    source = Path("src/manager/dashboard.html").read_text()
    renderer = (
        "function plot("
        + source.split("function plot(", 1)[1].split("function visiblePrices()", 1)[0]
    )
    script = (
        r"""
const assert=require('node:assert/strict');
const paths=[],labels=[];let points=[];
const c={scale(){},fillText(t){labels.push(t)},save(){},restore(){},setLineDash(){},
beginPath(){points=[]},moveTo(x,y){points.push([x,y])},lineTo(x,y){points.push([x,y])},
stroke(){paths.push({color:this.strokeStyle,points:[...points]})}};
const $=()=>({getBoundingClientRect:()=>({width:700,height:280}),getContext:()=>c});
const window={devicePixelRatio:1},data={now:'2026-10-21T10:00:00Z'};
const bounds={start:'2026-10-20T22:00:00Z',end:'2026-10-21T22:00:00Z',ticks:[
{timestamp:'2026-10-20T22:00:00Z',label:'00:00'},{timestamp:'2026-10-21T22:00:00Z',label:'24:00'}]};
"""
        + renderer
        + r"""
plot('energy',[{timestamp:'2026-10-21T04:00:00Z',load_kw:1},{timestamp:'2026-10-21T10:00:00Z',load_kw:2}],[{value:r=>r.load_kw,color:'load'}],false,bounds);
const load=paths.find(p=>p.color==='load').points;
assert.equal(load[0][0],42+(700-42-40)*.25);
assert.equal(load[1][0],42+(700-42-40)*.5,'Noon must be halfway across; leave afternoon blank');
assert.ok(labels.includes('00:00')&&labels.includes('24:00'));
labels.length=0;plot('energy',[],[{value:r=>r.load_kw,color:'load'}],false,bounds);
assert.ok(labels.includes('00:00')&&labels.includes('24:00'),'Keep full-day axis before first measurement');
"""
    )
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
