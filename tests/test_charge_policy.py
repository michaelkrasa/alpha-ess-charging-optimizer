"""Executable risk/maintenance policies and durable partial-charge deadlines."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from src.manager.charge_policy import (
    archive_forecasts,
    record_errors,
    buffer_request,
    observe_full_charge,
    maintenance_due,
)
from src.manager.domain import PRAGUE, Slot, Telemetry
from src.manager.settings import Settings
from src.manager.store import SQLiteStore
from src.manager.partial_charge import partial_step, mask_stopped_charge
from src.manager.controller import Controller
from tests.test_daily import fake_device


def settings(**kwargs):
    return Settings(
        control_mode="legacy_daily",
        standard_planner="emhass",
        charge_kw=8.6,
        discharge_kw=8.6,
        effective_capacity_kwh=14.2,
        **kwargs,
    )


def inputs():
    t = datetime(2026, 10, 20, 2, tzinfo=PRAGUE)
    slots = [
        Slot(
            (t + timedelta(minutes=15 * i)).isoformat(),
            0,
            0.20 if i < 4 else 0.4,
            0.03,
            1,
            0,
            "NT",
        )
        for i in range(20)
    ]
    return slots, Telemetry(t.isoformat(), 10, 14.2, 1, 0, 1, 0)


@pytest.mark.parametrize("pv,load,soc", [(0, 1, 10), (6, 1, 99), (12, 1, 50)])
def test_actual_partial_solver_replays_fixed_power_prefix_then_hold(pv, load, soc):
    pytest.importorskip("emhass")
    from src.manager.emhass_mpc import EmhassPlanner

    slots, t = inputs()
    slots = [replace(r, solar_kw=pv, load_kw=load) for r in slots]
    s = settings(charge_step_minutes=5)
    planner = EmhassPlanner(s)
    ds = planner.plan(slots, replace(t, soc_percent=soc))
    energy = 14.2 * soc / 100
    for r, d in zip(slots, ds):
        flow = partial_step(
            energy,
            load,
            pv,
            d.charge_minutes or 0,
            100,
            14.2,
            s,
            d.action == "self_consume",
        )
        assert d.soc_percent == pytest.approx(flow[0] / 14.2 * 100)
        assert flow[1] + pv * 0.25 + flow[3] == pytest.approx(
            load * 0.25 + flow[2] + flow[4] + flow[5]
        )
        energy = flow[0]
    assert next(iter(planner.cache.values())).prob.is_dpp()
    if pv == 0:
        assert any(d.charge_minutes in (5, 10) for d in ds)


def test_bounded_risk_reward_adds_inventory_within_accepted_premium():
    pytest.importorskip("emhass")
    from src.manager.emhass_mpc import EmhassPlanner

    slots, t = inputs()
    s = settings(charge_step_minutes=5, hedge_enabled=True, hedge_premium_eur=0.15)
    planner = EmhassPlanner(s)
    ds = planner.plan(slots, t)
    info = planner.last_policy["buffer"]
    assert info["source"].startswith("provisional")
    assert 0 < info["effective_buffer_kwh"] <= s.hedge_max_kwh
    assert all(10 - 1e-5 <= d.soc_percent <= 100 + 1e-5 for d in ds)
    # The bounded extra objective can trade at most its declared premium.
    assert (
        next(iter(planner.cache.values())).hedged_energy.value
        <= info["effective_buffer_kwh"] * 1000 + 1e-5
    )


def test_errors_preserve_correlations_and_ignore_future_days():
    slots, t = inputs()
    s = settings(hedge_enabled=True, hedge_min_days=3)
    errors = []
    for ago in range(1, 4):
        for i, r in enumerate(slots):
            stamp = datetime.fromisoformat(r.timestamp) - timedelta(days=ago)
            errors.append(
                {
                    "timestamp": stamp.isoformat(),
                    "error_kw": 2 if i == 4 else -2 if i == 5 else 0,
                }
            )
    errors.extend({"timestamp": r.timestamp, "error_kw": 1000} for r in slots)
    result = buffer_request(slots, s, errors)
    assert result["sample_days"] == 3
    assert result["source"].startswith("empirical")
    # Prefix shortage survives later cancellation; no sum of independent quantiles.
    assert result["buffer_kwh"] == pytest.approx(0.5 / 0.95)


def test_original_long_lead_forecasts_and_ev_separated_errors(tmp_path):
    slots, t = inputs()
    store = SQLiteStore(tmp_path / "db")
    target = datetime.fromisoformat(slots[0].timestamp)
    archive_forecasts(store, slots, target - timedelta(hours=3))
    archive_forecasts(
        store, [replace(r, load_kw=99) for r in slots], target - timedelta(hours=1)
    )
    assert (
        store.get("risk_forecasts")[
            target.astimezone(__import__("datetime").timezone.utc).isoformat()
        ]["load_kw"]
        == 1
    )
    rows = [
        {
            "uploadTime": (target + timedelta(minutes=5 * i)).isoformat(),
            "load": 2000,
            "ppv": 0,
            "gridCharge": 2000,
            "feedIn": 0,
            "cbat": 50,
        }
        for i in range(4)
    ]
    record_errors(store, rows, target + timedelta(minutes=15), False)
    assert store.get("risk_errors")[0]["error_kw"] == 1
    assert len(store.get("risk_errors")) == 1


def test_full_charge_is_measured_and_taper_requires_distinct_samples(tmp_path):
    slots, t = inputs()
    store = SQLiteStore(tmp_path / "db")
    now = datetime.fromisoformat(t.timestamp)
    s = settings(full_charge_interval_days=14)
    assert maintenance_due(s, {}, now)
    full = replace(t, soc_percent=100, battery_kw=-0.1)
    observe_full_charge(store, full, now)
    observe_full_charge(store, full, now + timedelta(minutes=1))
    assert "last_calibration_at" not in store.get("full_charge")
    later = replace(full, timestamp=(now + timedelta(minutes=5)).isoformat())
    observe_full_charge(store, later, now + timedelta(minutes=5))
    assert not maintenance_due(s, store.get("full_charge"), now + timedelta(days=13))
    assert maintenance_due(s, store.get("full_charge"), now + timedelta(days=15))


def test_maintenance_actual_solve_reaches_full_at_low_price():
    pytest.importorskip("emhass")
    from src.manager.emhass_mpc import EmhassPlanner

    slots, t = inputs()
    slots = [
        replace(r, import_eur_kwh=0.1 if i < 10 else 0.4) for i, r in enumerate(slots)
    ]
    planner = EmhassPlanner(
        settings(charge_step_minutes=5, full_charge_interval_days=14)
    )
    ds = planner.plan(slots, t, full_charge_due=True)
    assert any(d.soc_percent >= 99.999 for d in ds[:10])
    assert planner.last_policy["maintenance"]["target_soc"] == 100


def test_mask_removes_active_quarter_but_retains_later_charge():
    c = {
        "gridCharge": 1,
        "batHighCap": 100,
        "timeChaf1": "10:45",
        "timeChae1": "11:30",
        "timeChaf2": "13:00",
        "timeChae2": "14:00",
    }
    intent = {"start": "2026-10-20T10:45:00+02:00", "end": "2026-10-20T11:00:00+02:00"}
    masked = mask_stopped_charge(c, intent)
    assert (masked["timeChaf1"], masked["timeChae1"]) == ("11:00", "11:30")
    assert masked["timeChaf2"] == "13:00"


@pytest.mark.parametrize("restart,fail", [(False, False), (True, False), (True, True)])
async def test_stop_survives_restart_retries_failure_and_preserves_future(
    tmp_path, monkeypatch, restart, fail
):
    now = datetime(2026, 10, 20, 10, 50, tzinfo=PRAGUE)
    monkeypatch.setattr("src.manager.controller.utc_now", lambda: now)
    monkeypatch.setattr("src.manager.legacy_device.utc_now", lambda: now)
    store = SQLiteStore(tmp_path / "manager")
    d = fake_device(tmp_path / "device")
    d.current.update(
        gridCharge=1,
        timeChaf1="10:45",
        timeChae1="11:00",
        timeChaf2="13:00",
        timeChae2="13:30",
    )

    async def close():
        pass

    d.close = close
    s = settings(
        charge_step_minutes=5,
        early_stop_verified=True,
        device_control_verified=True,
        high_tariff_periods=[],
    )
    store.put(
        "control", {"enabled": True, "revision": "test", "restore_pending": False}
    )
    store.put(
        "early_stops",
        [
            {
                "start": (now - timedelta(minutes=5)).isoformat(),
                "stop_at": now.isoformat(),
                "end": (now + timedelta(minutes=10)).isoformat(),
                "revision": "test",
                "status": "pending",
            }
        ],
    )
    if restart:
        store = SQLiteStore(tmp_path / "manager")
    c = Controller(s, store, live=True, device_factory=lambda: d)
    original = d.write_charge
    if fail:

        async def broken(*args):
            raise ValueError("Synthetic stop outage")

        d.write_charge = broken
        with pytest.raises(ValueError, match="Synthetic"):
            await c.stop_charge_once()
        assert store.get("early_stops")[0]["status"] == "stopping"
        d.write_charge = original
    await c.stop_charge_once()
    assert store.get("early_stops")[0]["status"] == "completed"
    assert d.current["timeChaf1"] == "13:00"
    await c.stop_charge_once()
    assert len(d.writes) == 1


async def test_off_intent_never_restarts_or_writes_from_deadline(tmp_path, monkeypatch):
    now = datetime(2026, 10, 20, 10, 50, tzinfo=PRAGUE)
    monkeypatch.setattr("src.manager.controller.utc_now", lambda: now)
    store = SQLiteStore(tmp_path / "db")
    store.put(
        "early_stops",
        [
            {
                "start": (now - timedelta(minutes=5)).isoformat(),
                "stop_at": now.isoformat(),
                "end": (now + timedelta(minutes=10)).isoformat(),
                "revision": "old",
                "status": "pending",
            }
        ],
    )
    c = Controller(
        settings(charge_step_minutes=5),
        store,
        live=True,
        device_factory=lambda: pytest.fail("OFF created device"),
    )
    await c.stop_charge_once()


def test_daily_premium_reservation_survives_retry_restart_and_resets_next_day(tmp_path):
    from src.manager.charge_policy import reserve_premium

    store = SQLiteStore(tmp_path / "db")
    now = datetime(2026, 10, 20, 2, tzinfo=PRAGUE)
    risk = {"first_extra_minutes": 5, "forecast_premium_eur": 0.07}
    reserve_premium(store, risk, "first", now, 0.1)
    store = SQLiteStore(tmp_path / "db")
    reserve_premium(store, risk, "first", now, 0.1)
    assert store.get("hedge_budget")["reserved_eur"] == pytest.approx(0.07)
    reserve_premium(store, risk, "second", now, 0.1)
    assert store.get("hedge_budget")["reserved_eur"] == pytest.approx(0.1)
    reserve_premium(store, risk, "third", now + timedelta(days=1), 0.1)
    assert store.get("hedge_budget")["reserved_eur"] == pytest.approx(0.07)


def test_maintenance_does_not_early_stop_at_modeled_full_soc():
    pytest.importorskip("emhass")
    from src.manager.emhass_mpc import EmhassPlanner

    slots, t = inputs()
    planner = EmhassPlanner(
        settings(charge_step_minutes=5, full_charge_interval_days=14)
    )
    ds = planner.plan(slots, t, full_charge_due=True)
    target = planner.last_policy["maintenance"]["target_at"]
    i = next(i for i, d in enumerate(ds) if d.timestamp == target)
    assert ds[i].soc_percent == pytest.approx(100)
    assert ds[i].charge_minutes == ds[i + 1].charge_minutes == 15


async def test_fresh_soc_ceiling_stops_early_but_stale_soc_does_not(
    tmp_path, monkeypatch
):
    now = datetime(2026, 10, 20, 10, 48, tzinfo=PRAGUE)
    monkeypatch.setattr("src.manager.controller.utc_now", lambda: now)
    monkeypatch.setattr("src.manager.legacy_device.utc_now", lambda: now)
    store = SQLiteStore(tmp_path / "manager")
    d = fake_device(tmp_path / "device")
    d.current.update(gridCharge=1, timeChaf1="10:45", timeChae1="11:00")

    async def close():
        pass

    d.close = close
    store.put(
        "control", {"enabled": True, "revision": "test", "restore_pending": False}
    )
    store.put(
        "early_stops",
        [
            {
                "start": (now - timedelta(minutes=3)).isoformat(),
                "stop_at": (now + timedelta(minutes=2)).isoformat(),
                "end": (now + timedelta(minutes=12)).isoformat(),
                "revision": "test",
                "status": "pending",
                "soc_ceiling": 50,
            }
        ],
    )
    c = Controller(
        settings(
            charge_step_minutes=5,
            early_stop_verified=True,
            device_control_verified=True,
            high_tariff_periods=[],
        ),
        store,
        live=True,
        device_factory=lambda: d,
    )
    store.put(
        "telemetry",
        {"timestamp": (now - timedelta(minutes=10)).isoformat(), "soc_percent": 60},
    )
    await c.stop_charge_once()
    assert not d.writes
    store.put("telemetry", {"timestamp": now.isoformat(), "soc_percent": 50})
    await c.stop_charge_once()
    assert store.get("early_stops")[0]["status"] == "completed"
    assert not d.current["gridCharge"]


def test_failed_optional_risk_solve_retains_certified_nominal_physics(monkeypatch):
    pytest.importorskip("emhass")
    from src.manager.emhass_mpc import EmhassPlanner, ApplianceOptimization

    original = ApplianceOptimization.perform_naive_mpc_optim
    calls = 0

    def solve(obj, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            obj.optim_status = "Infeasible"
            return None
        return original(obj, *args, **kwargs)

    monkeypatch.setattr(ApplianceOptimization, "perform_naive_mpc_optim", solve)
    slots, t = inputs()
    planner = EmhassPlanner(settings(charge_step_minutes=5, hedge_enabled=True))
    ds = planner.plan(slots, t)
    assert planner.last_solve["status"] == "Optimal"
    assert planner.last_solve["risk_fallback"]
    assert "nominal" in planner.last_policy["buffer"]["status"]
    assert len(ds) == len(slots)
    assert all(10 - 1e-6 <= d.soc_percent <= 100 + 1e-6 for d in ds)
    assert planner.plan(slots, t)  # Cached solver recovers on the next call.


def test_partial_live_readiness_requires_running_deadline_executor(
    tmp_path, monkeypatch
):
    now = datetime(2026, 10, 20, 10, 50, tzinfo=PRAGUE)
    monkeypatch.setattr("src.manager.controller.utc_now", lambda: now)
    c = Controller(
        settings(
            charge_step_minutes=5,
            early_stop_verified=True,
            device_control_verified=True,
            high_tariff_periods=[],
        ),
        SQLiteStore(tmp_path / "db"),
        live=True,
    )
    assert any("continuous" in gap for gap in c.readiness())
    c.deadline_executor_active = True
    assert not c.readiness()


async def test_expired_deadline_is_not_reported_as_confirmed_stop(
    tmp_path, monkeypatch
):
    now = datetime(2026, 10, 20, 11, 1, tzinfo=PRAGUE)
    monkeypatch.setattr("src.manager.controller.utc_now", lambda: now)
    store = SQLiteStore(tmp_path / "manager")
    d = fake_device(tmp_path / "device")

    async def close():
        pass

    d.close = close
    store.put(
        "control", {"enabled": True, "revision": "test", "restore_pending": False}
    )
    store.put(
        "early_stops",
        [
            {
                "start": (now - timedelta(minutes=16)).isoformat(),
                "stop_at": (now - timedelta(minutes=11)).isoformat(),
                "end": (now - timedelta(minutes=1)).isoformat(),
                "revision": "test",
                "status": "stopping",
            }
        ],
    )
    c = Controller(
        settings(
            charge_step_minutes=5,
            early_stop_verified=True,
            device_control_verified=True,
        ),
        store,
        live=True,
        device_factory=lambda: d,
    )
    await c.stop_charge_once()
    assert store.get("early_stops")[0]["status"] == "expired"
    assert not d.writes
    assert "confirmed_at" not in store.get("early_stops")[0]
    c.device_factory = lambda: pytest.fail("Expired intent retried")
    await c.stop_charge_once()


def test_partial_charge_cannot_hide_instantaneous_import_limit():
    pytest.importorskip("emhass")
    from src.manager.emhass_mpc import EmhassPlanner

    slots, t = inputs()
    # Five minutes would average under 6 kW, but actual 1+8.6 kW must be rejected.
    planner = EmhassPlanner(settings(charge_step_minutes=5, import_limit_kw=6))
    assert all(d.action != "charge" for d in planner.plan(slots, t))


async def test_commissioning_stop_borrows_and_retains_existing_writer_lease(
    tmp_path, monkeypatch
):
    now = datetime(2026, 10, 23, 14, 5, tzinfo=PRAGUE)
    monkeypatch.setattr("src.manager.controller.utc_now", lambda: now)
    monkeypatch.setattr("src.manager.legacy_device.utc_now", lambda: now)
    store = SQLiteStore(tmp_path / "manager")
    d = fake_device(tmp_path / "device")
    d.current.update(gridCharge=1, timeChaf1="14:00", timeChae1="14:15")

    async def close():
        pass

    d.close = close
    store.put(
        "control", {"enabled": True, "revision": "test", "restore_pending": False}
    )
    store.put(
        "early_stops",
        [
            {
                "start": (now - timedelta(minutes=5)).isoformat(),
                "stop_at": now.isoformat(),
                "end": (now + timedelta(minutes=10)).isoformat(),
                "revision": "test",
                "status": "pending",
            }
        ],
    )
    c = Controller(
        settings(
            charge_step_minutes=5,
            early_stop_verified=True,
            device_control_verified=True,
        ),
        store,
        live=True,
        device_factory=lambda: d,
    )
    store.acquire("commissioning", ttl=600)
    await c.stop_charge_once(lease_owner="commissioning")
    assert store.is_owner("commissioning")
    assert store.get("early_stops")[0]["status"] == "completed"
    assert not d.current["gridCharge"]
