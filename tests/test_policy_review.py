"""Behavior regressions found during the session-wide independent review."""

from dataclasses import replace
from datetime import datetime, timedelta
import pytest
from src.manager.controller import Controller
from src.manager.domain import PRAGUE
from src.manager.store import SQLiteStore
from tests.test_charge_policy import inputs, settings
from tests.test_daily import fake_device


async def test_off_restores_native_even_with_an_active_partial_deadline(
    tmp_path, monkeypatch
):
    now = datetime(2026, 10, 20, 10, 48, tzinfo=PRAGUE)
    monkeypatch.setattr("src.manager.controller.utc_now", lambda: now)
    monkeypatch.setattr("src.manager.legacy_device.utc_now", lambda: now)
    store = SQLiteStore(tmp_path / "state")
    store.put("control", {"enabled": False, "restore_pending": True, "revision": "off"})
    store.put(
        "early_stops",
        [
            {
                "start": (now - timedelta(minutes=3)).isoformat(),
                "stop_at": (now + timedelta(minutes=2)).isoformat(),
                "end": (now + timedelta(minutes=12)).isoformat(),
                "status": "pending",
                "revision": "on",
            }
        ],
    )
    device = fake_device(tmp_path / "device")
    device.current.update(gridCharge=1, timeChaf1="10:45", timeChae1="11:00")

    async def close():
        pass

    async def failed_observation(*args):
        raise ValueError("synthetic unavailable telemetry")

    device.close = close
    c = Controller(
        settings(
            charge_step_minutes=5,
            early_stop_verified=True,
            device_control_verified=True,
        ),
        store,
        live=True,
        device_factory=lambda: device,
    )
    monkeypatch.setattr(c, "observe", failed_observation)
    await c.run_once()
    assert not device.current["gridCharge"]
    assert not store.get("control")["restore_pending"]


def test_insurance_hold_reserves_the_daily_premium(tmp_path):
    from src.manager.emhass_mpc import EmhassPlanner
    from src.manager.charge_policy import reserve_premium

    slots, t = inputs()
    t = replace(t, soc_percent=50)
    cfg = settings(charge_step_minutes=5, hedge_enabled=True)
    planner = EmhassPlanner(cfg)
    decisions = planner.plan(slots, t)
    risk = planner.last_policy["buffer"]
    assert decisions[0].action == "hold"
    assert risk["forecast_premium_eur"] > 0 and risk["first_extra_minutes"] == 0
    store = SQLiteStore(tmp_path / "state")
    reserve_premium(
        store, risk, slots[0].timestamp, datetime.fromisoformat(slots[0].timestamp), 0.1
    )
    assert store.get("hedge_budget", {}).get("reserved_eur", 0) > 0


def test_import_blocked_maintenance_keeps_a_feasible_nominal_plan():
    from src.manager.emhass_mpc import EmhassPlanner

    slots, t = inputs()
    planner = EmhassPlanner(
        settings(charge_step_minutes=5, import_limit_kw=6, full_charge_interval_days=14)
    )
    result = planner.plan(slots, t, full_charge_due=True)
    assert result and all(d.action != "charge" for d in result)
    assert str(planner.last_policy["maintenance"]).startswith("pending:")


async def test_overdue_maintenance_uses_earliest_fill_not_a_rolling_cheap_window(
    tmp_path, monkeypatch
):
    now = datetime(2026, 10, 20, 2, tzinfo=PRAGUE)
    monkeypatch.setattr("src.manager.controller.utc_now", lambda: now)
    slots, t = inputs()
    t = replace(t, soc_percent=50)
    slots = [replace(r, import_eur_kwh=0.30 - 0.002 * i) for i, r in enumerate(slots)]
    store = SQLiteStore(tmp_path / "state")
    store.put("full_charge", {"due_since": (now - timedelta(days=2)).isoformat()})
    c = Controller(settings(charge_step_minutes=5, full_charge_interval_days=14), store)
    await c.standard_plan(slots, t, None)
    first = datetime.fromisoformat(c._emhass.last_policy["maintenance"]["target_at"])
    # With zero PV, four quarters suffice to fill from50%; choose that earliest
    # physically feasible fill rather than repeatedly selecting four hours away.
    assert first <= now + timedelta(minutes=60)


def test_insurance_revisions_keep_per_quarter_maximum_without_refunds():
    from src.manager.charge_policy import premium_reservation

    now = datetime(2026, 10, 20, 2, tzinfo=PRAGUE)
    quarter = now.isoformat()

    def book(bank, amount):
        return premium_reservation(
            bank,
            {"first_action_changed": True, "forecast_premium_eur": amount},
            quarter,
            now,
            0.1,
        )

    bank = book({}, 0.02)
    assert book(bank, 0.02)["reserved_eur"] == pytest.approx(0.02)
    bank = book(bank, 0.07)
    assert bank["reserved_eur"] == pytest.approx(0.07)
    assert book(bank, 0.01)["reserved_eur"] == pytest.approx(0.07)
    assert book(bank, 0.15)["reserved_eur"] == pytest.approx(0.1)
    legacy = {"day": str(now.date()), "reserved_eur": 0.02, "quarters": [quarter]}
    assert book(legacy, 0.07)["reserved_eur"] == pytest.approx(0.09)


def test_stop_journal_keeps_committed_deadline_and_masks_ambiguous_stop():
    from src.manager.partial_charge import prepare_stops

    now = datetime(2026, 10, 20, 10, 50, tzinfo=PRAGUE)
    intent = {
        "start": (now - timedelta(minutes=5)).isoformat(),
        "stop_at": now.isoformat(),
        "end": (now + timedelta(minutes=10)).isoformat(),
        "status": "stopping",
        "revision": "same",
    }
    charge = {
        "gridCharge": 1,
        "batHighCap": 100,
        "timeChaf1": "10:45",
        "timeChae1": "11:15",
        "timeChaf2": "13:00",
        "timeChae2": "13:15",
    }
    proposed = {**intent, "stop_at": (now + timedelta(minutes=5)).isoformat()}
    journal, masked = prepare_stops([intent], [proposed], charge, "same", now)
    assert journal == [intent]
    assert (masked["timeChaf1"], masked["timeChae1"]) == ("11:00", "11:15")
    assert masked["timeChaf2"] == "13:00"


def test_urgent_full_charge_begins_now_and_converges_on_one_completion_time():
    from src.manager.emhass_mpc import EmhassPlanner

    slots, t = inputs()
    t = replace(t, soc_percent=50)
    slots = [replace(r, import_eur_kwh=0.30 - 0.002 * i) for i, r in enumerate(slots)]
    cfg = settings(charge_step_minutes=5, full_charge_interval_days=14)
    planner = EmhassPlanner(cfg)
    deadline = datetime.fromisoformat(slots[0].timestamp) - timedelta(hours=1)
    targets = []
    for _ in range(3):
        ds = planner.plan(slots, t, full_charge_due=True, maintenance_deadline=deadline)
        assert ds[0].action == "charge" and ds[0].charge_minutes > 0
        targets.append(planner.last_policy["maintenance"]["target_at"])
        t = replace(t, soc_percent=ds[0].soc_percent, timestamp=slots[1].timestamp)
        slots = slots[1:]
    assert len(set(targets)) == 1


@pytest.mark.parametrize("borrowed", [False, True])
async def test_stop_cleanup_releases_only_owned_lease_if_close_fails(
    tmp_path, monkeypatch, borrowed
):
    now = datetime(2026, 10, 20, 10, 50, tzinfo=PRAGUE)
    monkeypatch.setattr("src.manager.controller.utc_now", lambda: now)
    monkeypatch.setattr("src.manager.legacy_device.utc_now", lambda: now)
    store = SQLiteStore(tmp_path / "state")
    store.put("control", {"enabled": True, "revision": "same"})
    store.put(
        "early_stops",
        [
            {
                "start": (now - timedelta(minutes=5)).isoformat(),
                "stop_at": now.isoformat(),
                "end": (now + timedelta(minutes=10)).isoformat(),
                "status": "pending",
                "revision": "same",
            }
        ],
    )
    device = fake_device(tmp_path / "device")
    device.current.update(gridCharge=1, timeChaf1="10:45", timeChae1="11:00")

    async def close():
        raise RuntimeError("synthetic close failure")

    device.close = close
    c = Controller(
        settings(
            charge_step_minutes=5,
            early_stop_verified=True,
            device_control_verified=True,
        ),
        store,
        live=True,
        device_factory=lambda: device,
    )
    if borrowed:
        store.acquire("commission", ttl=3600)
    with pytest.raises(RuntimeError, match="synthetic close failure"):
        await c.stop_charge_once(lease_owner="commission" if borrowed else None)
    assert store.get("early_stops")[0]["status"] == "completed"
    assert store.is_owner("commission") is borrowed
    if not borrowed:
        assert store.get("lease") is None
