"""API retry and periodic transmission fencing, with no network requests."""

from datetime import datetime, timezone
from types import SimpleNamespace

from alphaess.alphaess import AlphaESSApiError
import pytest

from src.manager.device import AlphaDevice, DeviceUnavailable
from src.manager.settings import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["native", "charge"])
async def test_periodic_off_during_rate_limit_wait_cancels_transmission(monkeypatch, action):
    device = object.__new__(AlphaDevice)
    device.serial = "test"
    device.last_call = 0
    allowed = True
    period = {
        "beginTime": "05:00",
        "endTime": "06:00",
        "chargeLimit": 100,
        "chargePower": 1000,
    }

    async def read_config():
        return {
            "chargeTimeList": [period],
            "dischargeTimeList": [period],
        }, {"batUseCap": 10, "ctrDis": 0, "legacy_grid_charge": 0}

    async def unexpected_write(*args, **kwargs):
        raise AssertionError("Cancelled periodic command reached transport")

    async def sleep(_):
        nonlocal allowed
        allowed = False

    monkeypatch.setattr(device, "read_config", read_config)
    monkeypatch.setattr("src.manager.device.time.monotonic", lambda: 0)
    monkeypatch.setattr("src.manager.device.asyncio.sleep", sleep)
    device.client = SimpleNamespace(setTimeChargeBySn=unexpected_write)
    with pytest.raises(DeviceUnavailable, match="before transmission"):
        if action == "native":
            await device.native(10, True, guard=lambda: allowed)
        else:
            decision = SimpleNamespace(
                action="charge",
                timestamp="2026-10-20T10:00:00+00:00",
                soc_percent=100,
                grid_charge_kw=2,
            )
            await device.apply(
                decision,
                Settings(device_control_verified=True),
                datetime(2026, 10, 20, 9, tzinfo=timezone.utc),
                guard=lambda: allowed,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("failures, expected_calls", [(1, 2), (2, 2)])
async def test_timestamp_read_retry_is_once_and_respects_spacing(
    monkeypatch, failures, expected_calls
):
    clock = [100.0]
    calls = []
    sleeps = []
    device = object.__new__(AlphaDevice)
    device.last_call = 0

    async def read(*args):
        calls.append(clock[0])
        if len(calls) <= failures:
            raise AlphaESSApiError(6006, msg="private information")
        return {"fresh": True}

    async def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr("src.manager.device.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("src.manager.device.asyncio.sleep", sleep)
    device.client = SimpleNamespace(getLastPowerData=read)
    if failures == 1:
        assert await device.call("getLastPowerData") == {"fresh": True}
    else:
        with pytest.raises(DeviceUnavailable, match="API 6006") as error:
            await device.call("getLastPowerData")
        assert "private information" not in str(error.value)
    assert len(calls) == expected_calls
    assert sleeps == [10]
    assert calls[1] - calls[0] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("method, code", [("setTimeChargeBySn", 6006), ("updateChargeConfigInfo", 6006), ("getESSList", 6017)])
async def test_mutations_and_other_read_errors_never_retry(monkeypatch, method, code):
    calls = []
    device = object.__new__(AlphaDevice)
    device.last_call = 0

    async def fail(*args):
        calls.append(method)
        raise AlphaESSApiError(code)

    monkeypatch.setattr("src.manager.device.time.monotonic", lambda: 100)
    device.client = SimpleNamespace(**{method: fail})
    with pytest.raises(DeviceUnavailable, match=f"API {code}"):
        await device.call(method)
    assert calls == [method]
