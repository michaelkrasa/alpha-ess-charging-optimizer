from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from src.manager.daily import plan_daily, step, simulate, in_window
from src.manager.device import DeviceUnavailable
from src.manager.domain import PRAGUE, UTC, Slot, Telemetry
from src.manager.legacy_device import LegacyDevice
from src.manager.settings import Settings
from src.manager.store import SQLiteStore
from src.manager.controller import Controller


def forecast():
    start = datetime(2026, 10, 20, 16, tzinfo=PRAGUE).astimezone(UTC)
    return [
        Slot(
            (start + timedelta(minutes=15 * i)).isoformat(),
            0,
            0.01 if i < 16 else 0.4,
            0,
            2,
            0,
            "NT",
        )
        for i in range(96)
    ]


def battery():
    return Telemetry(forecast()[0].timestamp, 10, 17.2, 2, 0, 2, 0)


@pytest.mark.parametrize("shift", [timedelta(minutes=1), timedelta(seconds=1), timedelta(microseconds=1)])
def test_standard_horizon_rejects_intervals_shifted_from_market_boundaries(shift):
    from src.manager.daily import validate_standard_horizon

    shifted = [
        replace(s, timestamp=(datetime.fromisoformat(s.timestamp) + shift).isoformat())
        for s in forecast()
    ]
    with pytest.raises(ValueError, match="aligned to price boundaries"):
        validate_standard_horizon(shifted)


def test_daily_plan_is_executable_in_two_standard_windows():
    p = plan_daily(forecast(), battery(), Settings())
    c = p["charge"]
    assert c["gridCharge"] == 1
    assert p["forecast_benefit_eur"] > 0
    assert len(p["decisions"]) == 96
    assert not {"chargePower", "hold"} & c.keys()
    for j in (1, 2):
        assert c[f"timeChaf{j}"] <= c[f"timeChae{j}"] <= "23:45"
        assert int(c[f"timeChaf{j}"][-2:]) % 15 == 0
    assert all(
        d["action"] in ("charge", "self_consume", "hold") for d in p["decisions"]
    )
    assert min(d["soc_percent"] for d in p["decisions"]) >= 10 - 1e-8
    assert max(d["soc_percent"] for d in p["decisions"]) <= 100 + 1e-8
    current = datetime.fromisoformat(p["valid_from"]) - timedelta(minutes=15)
    assert not in_window(
        current, [(c[f"timeChaf{j}"], c[f"timeChae{j}"]) for j in (1, 2)]
    )


def test_daily_plan_never_uses_unpublished_short_horizon():
    with pytest.raises(ValueError, match="complete published"):
        plan_daily(forecast()[:95], battery(), Settings())


def test_daily_plan_rejects_dst_and_nonfinite_forecasts():
    base = datetime(2026, 10, 24, 16, tzinfo=PRAGUE).astimezone(UTC)
    values = [
        replace(s, timestamp=(base + timedelta(minutes=15 * i)).isoformat())
        for i, s in enumerate(forecast())
    ]
    with pytest.raises(ValueError, match="DST"):
        plan_daily(values, battery(), Settings())
    values = forecast()
    values[0] = replace(values[0], load_kw=float("nan"))
    with pytest.raises(ValueError, match="Non-finite"):
        plan_daily(values, battery(), Settings())


def test_flat_daily_price_keeps_native_operation():
    values = [replace(s, import_eur_kwh=0.1) for s in forecast()]
    assert plan_daily(values, battery(), Settings())["charge"]["gridCharge"] == 0


def test_standard_api_does_not_pretend_to_limit_charge_power():
    s = Settings(import_limit_kw=3)
    score, _ = simulate(forecast(), battery(), s, [("16:00", "20:00")], 100)
    assert score == float("inf")
    flow = step(1.72, 2, 0, True, 100, 17.2, s)
    assert flow[1] / 0.25 == 7  # Unexpected demand needs appliance-side protection.


def test_native_physical_step_conserves_energy_and_cannot_export_battery():
    s = Settings()
    e, imported, exported, d, g, p = step(8.6, 3, 0, False, 100, 17.2, s)
    assert e == pytest.approx(8.6 - d / s.discharge_efficiency)
    assert imported + d == pytest.approx(0.75)
    assert exported == g == p == 0


def fake_device(tmp_path, fail_write=False, mismatch=False):
    d = object.__new__(LegacyDevice)
    d.store = SQLiteStore(tmp_path / "db")
    d.serial = "test"
    d.writes = []
    d.discharge = dict(
        ctrDis=0,
        batUseCap=10,
        timeDisf1="00:00",
        timeDise1="00:00",
        timeDisf2="00:00",
        timeDise2="00:00",
    )
    d.current = {
        "gridCharge": 0,
        "batHighCap": 100,
        "timeChaf1": "00:00",
        "timeChae1": "00:00",
        "timeChaf2": "00:00",
        "timeChae2": "00:00",
    }

    async def call(name, *args, **kwargs):
        assert name != "getTimeChargeBySn"
        if name == "getDisChargeConfigInfo":
            return dict(d.discharge)
        if name == "getChargeConfigInfo":
            return dict(d.current)
        if name == "updateChargeConfigInfo":
            guard = kwargs.pop("_write_guard")
            assert guard()
            d.writes.append(kwargs)
            if fail_write:
                raise DeviceUnavailable("Timed out; outcome unknown")
            if not mismatch:
                d.current = dict(kwargs)
            return None
        if name == "updateDisChargeConfigInfo":
            guard = kwargs.pop("_write_guard")
            assert guard()
            d.writes.append(kwargs)
            if fail_write:
                raise DeviceUnavailable("Timed out; outcome unknown")
            if not mismatch:
                d.discharge = dict(kwargs)
            return None
        raise AssertionError(name)

    d.call = call
    return d


@pytest.mark.asyncio
async def test_daily_write_is_read_back_and_does_not_probe_periodic_api(tmp_path):
    d = fake_device(tmp_path)
    requested = {
        **d.current,
        "gridCharge": 1,
        "timeChaf1": "01:00",
        "timeChae1": "03:00",
    }
    result = await d.write_charge(requested, d.current, lambda: True)
    assert result["schedule_verified"]
    assert not result["physical_execution_verified"]
    assert len(d.writes) == 1
    await d.write_charge(requested, d.current, lambda: True)
    assert len(d.writes) == 1


@pytest.mark.asyncio
async def test_off_respects_short_durable_api_cooldown(tmp_path):
    d = fake_device(tmp_path)
    requested = {
        **d.current,
        "gridCharge": 1,
        "timeChaf1": "01:00",
        "timeChae1": "03:00",
    }
    await d.write_charge(requested, d.current, lambda: True)
    with pytest.raises(DeviceUnavailable, match="cooldown"):
        await d.native(10, True)
    assert d.current["gridCharge"] == 1
    old = d.store.get("legacy_charge_write")
    old["attempted_at"] = (
        datetime.fromisoformat(old["attempted_at"]) - timedelta(seconds=20)
    ).isoformat()
    d.store.put("legacy_charge_write", old)
    await d.native(10, True)
    assert d.current["gridCharge"] == 0


@pytest.mark.asyncio
async def test_timeout_retains_budget_across_restart(tmp_path):
    d = fake_device(tmp_path, fail_write=True)
    requested = {
        **d.current,
        "gridCharge": 1,
        "timeChaf1": "01:00",
        "timeChae1": "03:00",
    }
    with pytest.raises(DeviceUnavailable, match="unknown"):
        await d.write_charge(requested, d.current, lambda: True)
    d.store = SQLiteStore(tmp_path / "db")
    with pytest.raises(DeviceUnavailable, match="cooldown"):
        await d.write_charge(requested, d.current, lambda: True)
    assert len(d.writes) == 1


@pytest.mark.asyncio
async def test_readback_mismatch_never_claims_acceptance(tmp_path):
    d = fake_device(tmp_path, mismatch=True)
    with pytest.raises(DeviceUnavailable, match="unconfirmed"):
        await d.write_charge(
            {**d.current, "gridCharge": 1, "timeChaf1": "01:00", "timeChae1": "02:00"},
            d.current,
            lambda: True,
        )
    assert d.store.get("legacy_charge_write")


@pytest.mark.asyncio
async def test_manual_control_change_cancels_daily_write(tmp_path):
    d = fake_device(tmp_path)
    with pytest.raises(DeviceUnavailable, match="cancelled"):
        await d.write_charge(
            {**d.current, "gridCharge": 1, "timeChaf1": "01:00", "timeChae1": "02:00"},
            d.current,
            lambda: False,
        )
    assert not d.writes
    assert not d.store.get("legacy_charge_write")


def test_daily_readiness_ignores_periodic_entitlement_but_requires_native_mode(
    tmp_path,
):
    store = SQLiteStore(tmp_path / "db")
    store.put("capability", {"periodic_available": False, "message": "API 6017"})
    controller = Controller(
        Settings(
            control_mode="legacy_daily",
            high_tariff_periods=[],
            device_control_verified=True,
        ),
        store,
        live=True,
    )
    assert not any("6017" in gap or "hold" in gap for gap in controller.readiness())
    store.put(
        "capability",
        {"mode": "legacy_daily", "available": True, "discharge_time_control": 1},
    )
    assert not any("discharge time" in gap for gap in controller.readiness())


@pytest.mark.asyncio
async def test_controller_uses_daily_backend_and_waits_during_write_budget(
    tmp_path, monkeypatch
):
    now = datetime(2026, 10, 20, 15, 45, tzinfo=PRAGUE).astimezone(UTC)
    monkeypatch.setattr("src.manager.controller.utc_now", lambda: now)
    store = SQLiteStore(tmp_path / "db")
    settings = Settings(
        control_mode="legacy_daily",
        high_tariff_periods=[],
        device_control_verified=True,
    )
    d = fake_device(tmp_path)
    d.closed = False

    async def telemetry():
        return replace(battery(), timestamp=now.isoformat())

    async def close():
        d.closed = True

    d.telemetry = telemetry
    d.close = close
    calls = []

    async def apply(plan, *args, **kwargs):
        assert "charge" in plan
        assert kwargs["guard"]()
        calls.append(plan)
        return {"status": "accepted"}

    d.apply_daily = apply
    c = Controller(settings, store, live=True, device_factory=lambda: d)

    async def slots(*args):
        return forecast(), []

    c.providers.slots = slots
    await c.set_enabled(True)
    first = await c.run_once()
    assert first["status"] == "active"
    assert calls and d.closed
    assert store.get("plan")["mode"] == "legacy_daily"
    store.put("legacy_charge_write", {"attempted_at": now.isoformat()})
    second = await c.run_once()
    assert second["status"] == "waiting"
    assert len(calls) == 1
    assert store.get("actuation")["status"] == "waiting"


@pytest.mark.asyncio
async def test_write_guard_is_checked_after_polling_delay(monkeypatch):
    from src.manager.device import AlphaDevice
    import time

    d = object.__new__(AlphaDevice)
    d.last_call = time.monotonic()
    allowed = True

    async def sleep(_):
        nonlocal allowed
        allowed = False

    monkeypatch.setattr("src.manager.device.asyncio.sleep", sleep)
    with pytest.raises(DeviceUnavailable, match="before transmission"):
        await d.call("updateChargeConfigInfo", _write_guard=lambda: allowed)


def test_profitable_night_fills_battery_for_expensive_household_demand():
    start = datetime(2026, 10, 20, 16, tzinfo=PRAGUE).astimezone(UTC)
    inputs = []
    for i in range(128):
        t = start + timedelta(minutes=15 * i)
        hour = t.astimezone(PRAGUE).hour
        inputs.append(
            Slot(t.isoformat(), 0, 0.01 if 0 <= hour < 6 else 0.4, 0, 2, 0, "NT")
        )
    p = plan_daily(inputs, battery(), Settings())
    night = [
        d
        for d in p["decisions"]
        if datetime.fromisoformat(d["timestamp"]).astimezone(PRAGUE).hour < 6
    ]
    assert max(d["soc_percent"] for d in night) >= 99.9
    assert p["forecast_improvement_vs_legacy_eur"] >= -1e-6
    assert p["discharge"]["batUseCap"] == 10


def test_vectorized_candidate_score_matches_executable_physics():
    from src.manager.daily import score_candidates

    s = Settings()
    values = forecast()
    state = battery()
    for c, d in [
        ((("16:00", "20:00"),), (("20:00", "23:45"),)),
        ((), None),
        ((), (("20:00", "23:45"),)),
    ]:
        energy = state.capacity_kwh * state.soc_percent / 100
        cost = 0
        for slot in values:
            t = datetime.fromisoformat(slot.timestamp)
            energy, imp, exp, dis, grid, pv = step(
                energy,
                slot.load_kw,
                slot.solar_kw,
                in_window(t, c),
                100,
                state.capacity_kwh,
                s,
                d is None or in_window(t, d),
            )
            cost += (
                imp * slot.import_eur_kwh
                - exp * slot.export_eur_kwh
                + dis * s.wear_eur_per_kwh
            )
        terminal = max(
            0,
            min(x.import_eur_kwh for x in values) * s.discharge_efficiency
            - s.wear_eur_per_kwh,
        )
        assert score_candidates(values, state, s, [(c, d)])[0] == pytest.approx(
            cost - energy * terminal
        )


def test_abundant_solar_does_not_force_full_night_grid_charge():
    start = datetime(2026, 10, 20, 16, tzinfo=PRAGUE).astimezone(UTC)
    values = []
    for i in range(128):
        t = start + timedelta(minutes=15 * i)
        hour = t.astimezone(PRAGUE).hour
        values.append(
            Slot(
                t.isoformat(),
                0,
                0.1 if hour < 6 else 0.2,
                0.05,
                0.5,
                8 if 10 <= hour < 16 else 0,
                "NT",
            )
        )
    p = plan_daily(values, replace(battery(), soc_percent=50), Settings())
    night = [
        d
        for d in p["decisions"]
        if datetime.fromisoformat(d["timestamp"]).astimezone(PRAGUE).hour < 6
    ]
    assert max(d["soc_percent"] for d in night) < 99


def test_selected_forecast_score_always_includes_old_policy():
    import random

    rng = random.Random(20261020)
    for _ in range(5):
        values = [
            replace(
                s,
                import_eur_kwh=rng.uniform(0.02, 0.4),
                spot_eur_mwh=rng.uniform(20, 200),
                load_kw=rng.uniform(0.2, 4),
            )
            for s in forecast()
        ]
        p = plan_daily(values, battery(), Settings())
        assert (
            p["forecast_improvement_vs_legacy_eur"] is None
            or p["forecast_improvement_vs_legacy_eur"] >= -1e-6
        )


@pytest.mark.asyncio
async def test_pair_preflight_does_not_spend_other_endpoint_budget(tmp_path):
    d = fake_device(tmp_path)
    from src.manager.domain import utc_now

    d.store.put("legacy_charge_write", {"attempted_at": utc_now().isoformat()})
    charge = {**d.current, "gridCharge": 1, "timeChaf1": "01:00", "timeChae1": "05:00"}
    discharge = {**d.discharge, "ctrDis": 1, "timeDisf1": "06:00", "timeDise1": "12:00"}
    with pytest.raises(DeviceUnavailable, match="cooldown"):
        await d.replace_pair(charge, discharge, d.current, d.discharge, lambda: True)
    assert not d.writes


@pytest.mark.asyncio
async def test_invalid_second_payload_cannot_partially_replace_first(tmp_path):
    d = fake_device(tmp_path)
    discharge = {**d.discharge, "ctrDis": 1, "timeDisf1": "06:00", "timeDise1": "12:00"}
    charge = {**d.current, "gridCharge": 1, "timeChaf1": "01:07", "timeChae1": "05:00"}
    with pytest.raises(DeviceUnavailable, match="Invalid"):
        await d.replace_pair(charge, discharge, d.current, d.discharge, lambda: True)
    assert not d.writes


@pytest.mark.asyncio
async def test_pair_updates_both_and_off_clears_both_after_budgets(tmp_path):
    d = fake_device(tmp_path)
    charge = {**d.current, "gridCharge": 1, "timeChaf1": "01:00", "timeChae1": "05:00"}
    discharge = {**d.discharge, "ctrDis": 1, "timeDisf1": "06:00", "timeDise1": "12:00"}
    result = await d.replace_pair(
        charge, discharge, d.current, d.discharge, lambda: True
    )
    assert (
        result["schedule_verified"]
        and d.discharge["ctrDis"] == 1
        and d.current["gridCharge"] == 1
    )
    assert d.store.get("legacy_discharge_write") and d.store.get("legacy_charge_write")
    for key in ("legacy_discharge_write", "legacy_charge_write"):
        row = d.store.get(key)
        row["attempted_at"] = (
            datetime.fromisoformat(row["attempted_at"]) - timedelta(seconds=20)
        ).isoformat()
        d.store.put(key, row)
    await d.native(10, True)
    assert (
        d.current["gridCharge"] == 0
        and d.discharge["ctrDis"] == 0
        and d.discharge["batUseCap"] == 10
    )


def test_recent_peak_load_envelope_prevents_risky_cheap_charging():
    values = [replace(s, charging_load_ceiling_kw=19) for s in forecast()]
    p = plan_daily(values, battery(), Settings(import_limit_kw=20))
    assert p["charge"]["gridCharge"] == 0


def test_capacity_uncertainty_keeps_baseline_candidate_and_full_reserve():
    s = Settings(capacity_scenarios_kwh=[14.2, 15.48, 17.2])
    p = plan_daily(forecast(), battery(), s)
    assert p["capacity_scenarios_kwh"] == [14.2, 15.48, 17.2]
    assert p["forecast_improvement_vs_legacy_eur"] >= -1e-6
    assert all(d["soc_percent"] >= 10 - 1e-6 for d in p["decisions"])


def test_full_night_plan_fills_nominal_battery_despite_smaller_fitted_scale():
    start = datetime(2026, 10, 20, 16, tzinfo=PRAGUE).astimezone(UTC)
    slots = []
    for i in range(128):
        t = start + timedelta(minutes=15 * i)
        h = t.astimezone(PRAGUE).hour
        slots.append(Slot(t.isoformat(), 0, 0.01 if h < 6 else 0.4, 0, 2, 0, "NT"))
    settings = Settings(
        charge_kw=8.6,
        discharge_kw=8.6,
        effective_capacity_kwh=14.2,
        legacy_reference_capacity_kwh=15.48,
        capacity_scenarios_kwh=[14.2, 15.48, 17.2],
    )
    plan = plan_daily(slots, replace(battery(), capacity_kwh=14.2), settings)
    assert len(plan["decisions"]) == 96  # Executable daily horizon stays bounded.
    assert len(plan["outlook_decisions"]) == len(slots)  # Display tomorrow evening too.
    cp, dp = plan["charge"], plan["discharge"]
    c = [(cp[f"timeChaf{j}"], cp[f"timeChae{j}"]) for j in (1, 2)]
    d = [(dp[f"timeDisf{j}"], dp[f"timeDise{j}"]) for j in (1, 2)]
    energy = 17.2 * 0.1
    night = []
    for slot in slots[:96]:
        t = datetime.fromisoformat(slot.timestamp)
        energy, *_ = step(
            energy,
            slot.load_kw,
            slot.solar_kw,
            bool(cp["gridCharge"]) and in_window(t, c),
            100,
            17.2,
            settings,
            not dp["ctrDis"] or in_window(t, d),
        )
        if t.astimezone(PRAGUE).hour < 6:
            night.append(energy / 17.2 * 100)
    assert max(night) >= 99.9


def test_dominated_native_comparison_remains_json_serializable(monkeypatch):
    import json
    import numpy as np
    import src.manager.daily as daily

    old = ((("00:00", "02:00"),), None)
    monkeypatch.setattr(daily, "legacy_periods", lambda *args: old)

    def costs(slots, telemetry, settings, candidates, **kwargs):
        result = np.full(len(candidates), 20.0)
        result[0] = 10.0
        result[candidates.index(old)] = 0.0
        return result

    monkeypatch.setattr(daily, "score_candidates", costs)
    plan = daily.plan_daily(forecast(), battery(), Settings())
    assert plan["selected"] == "legacy"
    assert plan["forecast_benefit_eur"] == 10.0
    json.dumps(plan, allow_nan=False)
