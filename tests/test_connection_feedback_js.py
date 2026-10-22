"""Exercise displayed connection states with the actual dashboard function."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_connection_feedback_distinguishes_contact_measurement_and_failures():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for dashboard checks")
    source = Path("src/manager/dashboard.html").read_text()
    function = (
        "function connectionFeedback(s)"
        + source.split("function connectionFeedback(s)", 1)[1].split(
            "function feedback()", 1
        )[0]
    )
    script = (
        r"""
const assert=require('node:assert/strict');
const values={},elements={monitorDot:{className:''}};
const text=(id,value)=>values[id]=value,$=id=>elements[id],local=t=>t;
const now=Date.parse('2026-10-21T12:00:00Z');
const ageMinutes=t=>(now-Date.parse(t))/60000;
"""
        + function
        + r"""
let state={telemetry:{timestamp:'2026-10-21T11:57:00Z'},device_connection:{status:'connected',last_success_at:'2026-10-21T11:59:40Z'}};
assert.equal(connectionFeedback(state),true);
assert.equal(values.monitorStatus,'Connected · cloud API');
assert.ok(values.monitorDetail.includes('11:57:00'),'Display actual measurement time separately');
state.device_connection.status='error';
assert.equal(connectionFeedback(state),false);
assert.equal(values.monitorStatus,'Connection needs attention');
assert.ok(elements.monitorDot.className.includes('wait'));
state.device_connection.status='connected';state.device_connection.last_success_at='2026-10-21T11:55:00Z';
assert.equal(connectionFeedback(state),false);
assert.equal(values.monitorStatus,'Connection check overdue');
state.device_connection.last_success_at='2026-10-21T11:59:40Z';state.telemetry.timestamp='2026-10-21T11:00:00Z';
assert.equal(connectionFeedback(state),false);
assert.equal(values.monitorStatus,'Connected · data delayed');
assert.equal(connectionFeedback({}),false);
assert.equal(values.monitorStatus,'Checking connection');
"""
    )
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
