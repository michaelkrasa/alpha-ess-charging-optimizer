"""Quarter-hour replanning through the two-window API, not a daily write lock."""

from dataclasses import replace
from datetime import datetime, timedelta
import pytest
from src.manager.domain import UTC, PRAGUE, Slot, Telemetry
from src.manager.settings import Settings
from src.manager.store import SQLiteStore
from src.manager.controller import Controller
from src.manager.daily import plan_daily, in_window
from tests.test_daily import fake_device, battery


async def test_different_settings_and_off_can_be_written_seconds_apart(
    tmp_path, monkeypatch
):
    clock = [datetime(2026, 10, 21, 10, tzinfo=UTC)]
    monkeypatch.setattr("src.manager.legacy_device.utc_now", lambda: clock[0])
    d = fake_device(tmp_path)
    first = {**d.current, "gridCharge": 1, "timeChaf1": "01:00", "timeChae1": "03:00"}
    await d.write_charge(first, d.current, lambda: True)
    clock[0] += timedelta(seconds=20)
    second = {**first, "timeChae1": "03:15"}
    await d.write_charge(second, d.current, lambda: True)
    assert d.current == second
    clock[0] += timedelta(seconds=20)
    await d.native(10, True)
    assert not d.current["gridCharge"]
    assert len(d.writes) == 3


async def test_controller_replans_from_new_soc_before_publication_every_quarter(
    tmp_path, monkeypatch
):
    clock = [datetime(2026, 10, 21, 10, tzinfo=PRAGUE).astimezone(UTC)]
    monkeypatch.setattr("src.manager.controller.utc_now", lambda: clock[0])
    monkeypatch.setattr("src.manager.legacy_device.utc_now", lambda: clock[0])
    store = SQLiteStore(tmp_path / "manager.db")
    d = fake_device(tmp_path / "device")
    soc = [80]
    applied = []

    async def telemetry():
        return replace(battery(), timestamp=clock[0].isoformat(), soc_percent=soc[0])

    async def close():
        pass

    async def apply(plan, *args, guard):
        assert guard()
        applied.append(plan)
        store.put("legacy_charge_write", {"attempted_at": clock[0].isoformat()})
        return {"status": "accepted", "schedule_verified": True}

    d.telemetry = telemetry
    d.close = close
    d.apply_daily = apply
    c = Controller(
        Settings(
            control_mode="legacy_daily",
            high_tariff_periods=[],
            device_control_verified=True,
        ),
        store,
        live=True,
        device_factory=lambda: d,
    )

    async def forecast(*args):
        return [
            Slot(
                (clock[0] + timedelta(minutes=15 * (i + 1))).isoformat(),
                10 if i < 4 else 400,
                0.01 if i < 4 else 0.4,
                0,
                2,
                0,
                "NT",
            )
            for i in range(20)
        ], []

    c.providers.slots = forecast
    await c.set_enabled(True)
    assert (await c.run_once())["status"] == "active"
    old = store.get("plan")
    assert len(old["decisions"]) == 20
    clock[0] += timedelta(minutes=15)
    soc[0] = 20
    assert (await c.run_once())["status"] == "active"
    assert len(applied) == 2
    assert (
        store.get("plan")["decisions"][0]["timestamp"]
        != old["decisions"][0]["timestamp"]
    )
    assert not c.readiness()


def test_installed_ongoing_charge_is_a_candidate_to_avoid_repeated_pauses():
    start = datetime(2026, 10, 21, 10, 15, tzinfo=PRAGUE)
    rows = [
        Slot(
            (start + timedelta(minutes=15 * i)).isoformat(),
            10 if i < 7 else 500,
            0.01 if i < 7 else 0.5,
            0,
            8,
            0,
            "NT",
        )
        for i in range(16)
    ]
    installed = {
        "charge": {
            "gridCharge": 1,
            "batHighCap": 100,
            "timeChaf1": "08:00",
            "timeChae1": "12:00",
            "timeChaf2": "00:00",
            "timeChae2": "00:00",
        },
        "discharge": {
            "ctrDis": 1,
            "batUseCap": 10,
            "timeDisf1": "12:00",
            "timeDise1": "23:45",
            "timeDisf2": "00:00",
            "timeDise2": "00:00",
        },
    }
    p = plan_daily(
        rows,
        Telemetry(start.isoformat(), 90, 17.2, 8, 0, 8, 0),
        Settings(charge_kw=8.6),
        minimum_slots=4,
        current_schedule=installed,
    )
    assert p["charge"] == installed["charge"]
    assert in_window(
        start - timedelta(minutes=15),
        [(p["charge"]["timeChaf1"], p["charge"]["timeChae1"])],
    )
    assert datetime.fromisoformat(p["valid_until"]) == start + timedelta(hours=4)
