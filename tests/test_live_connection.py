"""Connection checks remain read-only and cannot compete with control writes."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.manager.controller import Controller
from src.manager.device import DeviceUnavailable
from src.manager.domain import UTC, Telemetry
from src.manager.settings import Settings
from src.manager.store import BusyError, SQLiteStore


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    now = datetime(2026, 10, 21, 12, 4, tzinfo=UTC)
    monkeypatch.setattr("src.manager.controller.utc_now", lambda: now)
    device = SimpleNamespace(reads=0, closed=False, fail=False)

    async def telemetry():
        device.reads += 1
        if device.fail:
            raise DeviceUnavailable("AlphaESS read failed")
        return Telemetry((now - timedelta(minutes=3)).isoformat(), 50, 17.2, 1, 1, 0, 0)

    async def close():
        device.closed = True

    device.telemetry = telemetry

    async def history(day):
        return []

    device.power_history = history
    device.close = close
    controller = Controller(
        Settings(),
        SQLiteStore(tmp_path / "state.db"),
        live=True,
        device_factory=lambda: device,
    )
    return controller, device, now


async def test_monitor_reads_api_without_planning_or_writing(monitor):
    c, d, now = monitor
    await c.monitor_once()
    link = c.store.get("device_connection")
    assert link["status"] == "connected"
    assert link["last_success_at"] == now.isoformat()
    assert (
        link["sample_at"] != link["last_success_at"]
    )  # Never relabel old data as live.
    assert c.store.get("telemetry")["timestamp"] == link["sample_at"]
    assert c.store.get("plan") is None
    assert c.store.get("actuation") is None
    assert d.closed and d.reads == 1
    c.store.acquire("next-writer")  # Lease always released.


async def test_daily_energy_read_is_cached_without_control_writes(monitor):
    c, d, now = monitor
    reads = []
    async def totals(day):
        reads.append(day)
        return {'theDate': str(day), 'epv': 7.9, 'eCharge': 6.5}
    d.daily_energy = totals
    await c.monitor_once()
    assert c.store.get('daily_energy')['solar_generated_kwh'] == 7.9
    c.store.put('device_connection', {'checked_at': (now-timedelta(minutes=2)).isoformat()})
    await c.monitor_once()
    assert len(reads) == 1
    assert c.store.get('actuation') is None


async def test_monitor_failure_preserves_last_success_and_is_visible(monitor):
    c, d, now = monitor
    c.store.put(
        "device_connection",
        {"last_success_at": (now - timedelta(minutes=5)).isoformat()},
    )
    d.fail = True
    with pytest.raises(DeviceUnavailable):
        await c.monitor_once()
    link = c.store.get("device_connection")
    assert link["status"] == "error"
    assert link["last_success_at"] == (now - timedelta(minutes=5)).isoformat()
    assert d.closed
    c.store.acquire("next-writer")


async def test_monitor_does_not_overlap_writer(monitor):
    c, d, _ = monitor
    c.store.acquire("writer")
    with pytest.raises(BusyError):
        await c.monitor_once()
    assert d.reads == 0
    assert c.store.is_owner("writer")


async def test_monitor_skips_recent_contact_and_control_boundary(monitor, monkeypatch):
    c, d, now = monitor
    c.store.put("device_connection", {"checked_at": now.isoformat()})
    await c.monitor_once()
    assert d.reads == 0
    monkeypatch.setattr(
        "src.manager.controller.utc_now", lambda: now.replace(minute=14)
    )
    await c.monitor_once()
    assert d.reads == 0


async def test_monitor_never_contacts_real_device_in_observation_mode(monitor):
    c, d, _ = monitor
    c.live = False
    await c.monitor_once()
    assert d.reads == 0


async def test_api_spacing_is_shared_between_monitor_and_control_clients(
    tmp_path, monkeypatch
):
    from src.manager.device import AlphaDevice

    store = SQLiteStore(tmp_path / "spacing.db")
    clock = [1000.0]
    waits = []
    monkeypatch.setattr("src.manager.device.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("src.manager.device.time.time", lambda: clock[0])

    async def sleep(delay):
        waits.append(delay)
        clock[0] += delay

    async def read():
        return {"ok": True}

    monkeypatch.setattr("src.manager.device.asyncio.sleep", sleep)
    clients = []
    for _ in range(2):
        client = object.__new__(AlphaDevice)
        client.store = store
        client.last_call = 0
        client.client = SimpleNamespace(getLastPowerData=read)
        clients.append(client)
    assert await clients[0].call("getLastPowerData") == {"ok": True}
    assert await clients[1].call("getLastPowerData") == {"ok": True}
    assert waits == [10.0]


async def test_monitor_finalizes_yesterday_savings_once_without_writes(monitor):
    c, d, now = monitor
    from src.manager.domain import PRAGUE, day_slots
    yesterday = now.astimezone(PRAGUE).date() - timedelta(days=1)
    c.store.put("eur_conversion", {"rate": 25.0, "published": str(yesterday)})
    c.store.put(f"prices:{c.settings.price_source}:{yesterday}", [100.0] * len(day_slots(yesterday)))
    asked = []

    async def history(day):
        asked.append(day)
        return [{"uploadTime": t.isoformat(), "load": 2000, "ppv": 0, "cbat": 50, "gridCharge": 0, "feedIn": 0}
                for t in day_slots(day)] if day == yesterday else []

    d.power_history = history
    await c.monitor_once()
    entry = c.store.get("savings")["days"][str(yesterday)]
    assert entry["final"] and entry["quarters"] == 96 and entry["saved_eur"] > 0
    c.store.put("device_connection", {"checked_at": (now - timedelta(minutes=2)).isoformat()})
    await c.monitor_once()
    assert asked.count(yesterday) == 1
    assert c.store.get("actuation") is None
