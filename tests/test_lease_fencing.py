"""An expired controller must not dispatch or overwrite its successor's state."""

from datetime import datetime, timezone

import pytest

from src.manager.controller import Controller
from src.manager.domain import Telemetry
from src.manager.settings import Settings
from src.manager.store import DynamoStore, SQLiteStore


def test_sqlite_expired_owner_cannot_release_successor(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("src.manager.store.time.time", lambda: clock[0])
    first = SQLiteStore(tmp_path / "manager.db")
    second = SQLiteStore(tmp_path / "manager.db")
    first.acquire("first")
    assert first.is_owner("first")
    assert not first.is_owner("other")
    clock[0] += 211
    assert first.is_owner("first")
    assert not first.is_owner("first", min_remaining=30)
    clock[0] -= 211
    clock[0] += 241
    assert not first.is_owner("first")
    second.acquire("second")
    first.release("first")
    assert second.is_owner("second")


def test_dynamo_owner_check_is_consistent_and_requires_unexpired_lease(monkeypatch):
    monkeypatch.setattr("src.manager.store.time.time", lambda: 1000.0)

    class Table:
        item = {"owner": "first", "until": 1001}

        def get_item(self, **kwargs):
            assert kwargs == {
                "Key": {"pk": "household", "sk": "lease"},
                "ConsistentRead": True,
            }
            return {"Item": self.item}

    store = object.__new__(DynamoStore)
    store.table = Table()
    assert store.is_owner("first")
    assert not store.is_owner("first", min_remaining=30)
    store.table.item["until"] = 1031
    assert store.is_owner("first", min_remaining=30)
    store.table.item["until"] = 1030
    assert not store.is_owner("first", min_remaining=30)
    assert not store.is_owner("second")
    store.table.item["until"] = 1000
    assert not store.is_owner("first")
    store.table.item = {}
    assert not store.is_owner("first")


@pytest.mark.asyncio
async def test_forecast_outliving_lease_cannot_dispatch_or_overwrite_successor(
    tmp_path, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr("src.manager.store.time.time", lambda: clock[0])
    store = SQLiteStore(tmp_path / "manager.db")
    now = datetime.now(timezone.utc)
    store.put("control", {"enabled": True, "restore_pending": False, "revision": "on"})
    store.put("capability", {"mode": "legacy_daily", "checked_at": now.isoformat()})
    successor_health = {"status": "active", "owner": "successor"}

    class Device:
        closed = False

        async def telemetry(self):
            return Telemetry(now.isoformat(), 50, 17.2, 1, 0, 1, 0)

        async def read_config(self):
            return {"gridCharge": 0}, {"batUseCap": 10, "ctrDis": 0}

        async def native(self, *args, **kwargs):
            raise AssertionError("Expired owner must not attempt fallback")

        async def apply_daily(self, *args, **kwargs):
            raise AssertionError("Expired owner must not dispatch")

        async def close(self):
            self.closed = True

    device = Device()
    controller = Controller(
        Settings(control_mode="legacy_daily", device_control_verified=True),
        store,
        live=True,
        device_factory=lambda: device,
    )

    async def slow_forecast(*args):
        clock[0] += 241
        store.acquire("successor")
        store.put("health", successor_health)
        return [], []

    monkeypatch.setattr(controller.providers, "slots", slow_forecast)
    result = await controller.run_once()
    assert result["status"] == "busy"
    assert store.get("health") == successor_health
    assert store.is_owner("successor")
    assert store.get("legacy_charge_write") is None
    assert store.get("legacy_discharge_write") is None
    assert device.closed


@pytest.mark.asyncio
async def test_off_transmission_guard_fences_expired_owner(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("src.manager.store.time.time", lambda: clock[0])
    store = SQLiteStore(tmp_path / "manager.db")
    successor_actuation = {"status": "accepted", "owner": "successor"}

    class Device:
        async def native(self, *args, guard):
            assert guard()
            clock[0] += 241
            store.acquire("successor")
            store.put("actuation", successor_actuation)
            assert not guard()
            raise RuntimeError("Transmission cancelled by guard")

        async def close(self):
            pass

    controller = Controller(
        Settings(device_control_verified=True),
        store,
        live=True,
        device_factory=Device,
    )
    result = await controller.set_enabled(False)
    assert result["restore_pending"]
    assert store.get("actuation") == successor_actuation
    assert store.is_owner("successor")


@pytest.mark.asyncio
async def test_off_guard_requires_enough_lease_for_request_timeout(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("src.manager.store.time.time", lambda: clock[0])
    store = SQLiteStore(tmp_path / "manager.db")

    class Device:
        async def native(self, *args, guard):
            assert guard()
            clock[0] += 211  # Owner is still valid, but only 29 seconds remain.
            lease = store.get("lease")
            assert store.is_owner(lease["owner"])
            assert not guard()
            raise RuntimeError("Cancelled before starting request near expiry")

        async def close(self):
            pass

    controller = Controller(
        Settings(device_control_verified=True), store, live=True, device_factory=Device
    )
    result = await controller.set_enabled(False)
    assert result["restore_pending"]
    assert store.get("actuation")["status"] == "pending"
    assert store.get("legacy_charge_write") is None
    assert store.get("legacy_discharge_write") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("readback_fails", [False, True])
async def test_fallback_outliving_lease_preserves_successor_status(
    tmp_path, monkeypatch, readback_fails
):
    clock = [1000.0]
    monkeypatch.setattr("src.manager.store.time.time", lambda: clock[0])
    store = SQLiteStore(tmp_path / "manager.db")
    now = datetime.now(timezone.utc)
    store.put("control", {"enabled": True, "restore_pending": False, "revision": "on"})
    store.put("capability", {"mode": "legacy_daily", "checked_at": now.isoformat()})
    successor_health = {"status": "active", "owner": "successor"}
    successor_actuation = {"status": "accepted", "owner": "successor"}

    class Device:
        async def telemetry(self):
            return Telemetry(now.isoformat(), 50, 17.2, 1, 0, 1, 0)

        async def read_config(self):
            return {"gridCharge": 0}, {"batUseCap": 10, "ctrDis": 0}

        async def native(self, *args, guard):
            assert guard()
            # The status/readback operation is still in flight after the
            # transmission guard. The vendor might already hold the write.
            clock[0] += 241
            store.acquire("successor")
            store.put("health", successor_health)
            store.put("actuation", successor_actuation)
            if readback_fails:
                raise RuntimeError("Readback failed after lease expiry")
            return {"status": "accepted"}

        async def close(self):
            pass

    controller = Controller(
        Settings(control_mode="legacy_daily", device_control_verified=True),
        store,
        live=True,
        device_factory=Device,
    )

    async def unavailable_forecast(*args):
        raise ValueError("Forecast unavailable")

    monkeypatch.setattr(controller.providers, "slots", unavailable_forecast)
    result = await controller.run_once()
    assert result["status"] == "busy"
    assert store.get("health") == successor_health
    assert store.get("actuation") == successor_actuation
    assert store.is_owner("successor")
