"""Actual EMHASS solves must match executable fixed-power battery physics."""

from datetime import datetime, timedelta

import pytest

pytest.importorskip("emhass")
from src.manager.daily import step
from src.manager.domain import UTC, Slot, Telemetry
from src.manager.emhass_mpc import EmhassPlanner
from src.manager.settings import Settings


@pytest.mark.parametrize(
    "pv,load,soc,buy",
    [(0, 2, 10, 0.1), (6, 1, 90, 0.3), (12, 1, 50, -0.05), (0, 25, 50, 0.01)],
)
def test_actual_solver_matches_appliance_with_solar_saturation_and_negative_prices(
    pv, load, soc, buy
):
    start = datetime(2026, 10, 21, 8, tzinfo=UTC)
    slots = [
        Slot(
            (start + timedelta(minutes=15 * i)).isoformat(),
            100,
            buy if i < 4 else 0.5,
            0.05,
            load,
            pv,
            "NT",
        )
        for i in range(16)
    ]
    state = Telemetry(start.isoformat(), soc, 14.2, load, pv, 0, 0)
    planner = EmhassPlanner(
        Settings(charge_kw=8.6, discharge_kw=8.6, effective_capacity_kwh=14.2)
    )
    result = planner.plan(slots, state)
    assert len(result) == 16
    assert all(10 - 1e-5 <= d.soc_percent <= 100 + 1e-5 for d in result)
    assert all(
        not (d.grid_charge_kw > 1e-5 and d.battery_load_kw > 1e-5) for d in result
    )
    assert all(d.battery_load_kw <= max(0, load - pv) + 1e-5 for d in result)
    if load > 20:
        assert any(d.grid_charge_kw > 0 for d in result)
    assert planner.plan(slots, state)  # Also exercise the cached parameterized solve.


def test_explicit_optional_import_constraint_is_honoured():
    start = datetime(2026, 10, 21, 8, tzinfo=UTC)
    slots = [
        Slot(
            (start + timedelta(minutes=15 * i)).isoformat(),
            0,
            0.01 if i < 4 else 0.5,
            0,
            2,
            0,
            "NT",
        )
        for i in range(16)
    ]
    state = Telemetry(start.isoformat(), 10, 14.2, 2, 0, 2, 0)
    s = Settings(
        charge_kw=8.6, discharge_kw=8.6, effective_capacity_kwh=14.2, import_limit_kw=4
    )
    assert all(d.grid_charge_kw == 0 for d in EmhassPlanner(s).plan(slots, state))


def test_actual_economic_objective_matches_eur_physical_replay_and_terminal_stock():
    start = datetime(2026, 10, 21, 8, tzinfo=UTC)
    slots = [
        Slot(
            (start + timedelta(minutes=15 * i)).isoformat(), 100, 0.3, 0.05, 2, 0, "NT"
        )
        for i in range(16)
    ]
    s = Settings(charge_kw=8.6, discharge_kw=8.6, effective_capacity_kwh=14.2)
    state = Telemetry(start.isoformat(), 80, 14.2, 2, 0, 2, 0)
    planner = EmhassPlanner(s)
    decisions = planner.plan(slots, state)
    energy, cost = 14.2 * 0.8, 0
    for slot, d in zip(slots, decisions):
        energy, imp, exp, dis, _, _ = step(
            energy,
            slot.load_kw,
            slot.solar_kw,
            d.action == "charge",
            100,
            14.2,
            s,
            d.action == "self_consume",
        )
        cost += (
            imp * slot.import_eur_kwh
            - exp * slot.export_eur_kwh
            + dis * s.wear_eur_per_kwh
        )
    terminal = 0.3 * 0.95 - 0.045
    expected_profit = -cost + (energy - 14.2 * 0.1) * terminal
    assert next(iter(planner.cache.values())).prob.value == pytest.approx(
        expected_profit, abs=1e-7
    )


def test_full_next_day_horizon_is_not_truncated_and_reports_solver_certificate():
    import math
    from src.manager.solver_check import verify

    start = datetime(2026, 10, 22, 13, 15, tzinfo=UTC)
    slots = []
    for i in range(131):
        t = start + timedelta(minutes=15 * i)
        hour = (t.hour + t.minute / 60) % 24
        solar = max(0, 4 * math.sin((hour - 6) * math.pi / 12))
        buy = 0.14 if hour < 5 or 11 <= hour < 15 else 0.4
        slots.append(Slot(t.isoformat(), 100, buy, 0.05,
                          0.6 + 0.4 * math.sin(i * 0.3) ** 2, solar, 'NT'))
    from dataclasses import asdict
    payload = {'slots': [asdict(r) for r in slots],
               'telemetry': asdict(Telemetry(start.isoformat(), 61.6, 17.2, 1, 1, 0, 0))}
    report = verify(payload, Settings(charge_kw=8.6, discharge_kw=8.6,
                                    effective_capacity_kwh=14.2))
    assert report['intervals'] == 131
    assert [r['cached'] for r in report['runs']] == [False, True]
    assert all(r['physical_replay'] == 'passed' for r in report['runs'])
    assert all(r['mip_gap'] <= 0.001 for r in report['runs'])
