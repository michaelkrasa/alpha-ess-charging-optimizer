from datetime import datetime, timedelta

import pytest

from src.manager.daily import payloads, step
from src.manager.domain import PRAGUE, UTC, Decision, Slot, Telemetry
from src.manager.emhass_schedule import schedule_from_decisions
from src.manager.legacy_device import LegacyDevice
from src.manager.settings import Settings


def example(start, actions):
    slots, decisions = [], []
    for i, action in enumerate(actions):
        t = (start + timedelta(minutes=15 * i)).isoformat()
        slots.append(Slot(t, 100, 0.2, 0.05, 2, 0, "NT"))
        decisions.append(Decision(t, action, 0, 0, 0, 50, 0.2, 0.05, 2, 0, "test"))
    return slots, decisions


def test_tomorrows_clock_does_not_activate_in_progress_quarter():
    s = Settings()
    start = datetime(2026, 10, 21, 12, 15, tzinfo=PRAGUE)
    actions = ["hold"] * 96
    actions[95] = "charge"  # Tomorrow12:00 would overlap today's12:00.
    slots, decisions = example(start, actions)
    cp, dp = payloads((), None, s)
    plan = schedule_from_decisions(
        decisions,
        slots,
        Telemetry(start.isoformat(), 50, 17.2, 2, 0, 2, 0),
        s,
        {"charge": cp, "discharge": dp},
    )
    assert plan["charge"]["gridCharge"] == 0
    assert plan["discharge"]["timeDisf1"] == "12:00"
    assert plan["discharge"]["timeDise1"] == "12:15"


def test_keeps_current_mode_and_encodes_first_rolling_action():
    s = Settings()
    start = datetime(2026, 10, 21, 12, 15, tzinfo=PRAGUE)
    slots, decisions = example(
        start, ["charge", "hold", "self_consume", "charge", "self_consume"]
    )
    cp, dp = payloads((), None, s)
    plan = schedule_from_decisions(
        decisions,
        slots,
        Telemetry(start.isoformat(), 50, 17.2, 2, 0, 2, 0),
        s,
        {"charge": cp, "discharge": dp},
    )
    assert not plan["first_action_projection"]
    LegacyDevice.validate_settings(plan["charge"], "charge")
    LegacyDevice.validate_settings(plan["discharge"], "discharge")
    c, d = plan["selected"]["charge"], plan["selected"]["discharge"]
    assert not any(a < y and x < b for a, b in c for x, y in d)


def test_unrepresentable_last_quarter_is_reported():
    s = Settings()
    start = datetime(2026, 10, 21, 23, 45, tzinfo=PRAGUE)
    slots, decisions = example(start, ["charge"] * 5)
    plan = schedule_from_decisions(
        decisions, slots, Telemetry(start.isoformat(), 50, 17.2, 2, 0, 2, 0), s
    )
    assert plan["first_action_projection"]
    assert plan["charge"]["gridCharge"] == 0


def test_ev_blocks_discharge_and_retains_entire_site_grid_bill():
    s = Settings(charge_kw=8.6, discharge_kw=8.6)
    e, imported, exported, discharge, grid, solar = step(
        8, 12, 0, False, 100, 14.2, s, discharge=False, duration_hours=5 / 60
    )
    assert e == 8
    assert imported == pytest.approx(1)
    assert exported == discharge == grid == solar == 0


def test_five_minute_physics_integrates_to_quarter_hour():
    s = Settings(charge_kw=8.6, discharge_kw=8.6)
    quarter = step(13, 1, 5, False, 100, 14.2, s)
    energy, totals = 13, [0] * 5
    for _ in range(3):
        flow = step(energy, 1, 5, False, 100, 14.2, s, duration_hours=1 / 12)
        energy = flow[0]
        totals = [a + b for a, b in zip(totals, flow[1:])]
    assert [energy, *totals] == pytest.approx(quarter)


def test_repeated_dst_clock_cannot_create_conflicting_pair():
    start = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    slots, decisions = example(
        start, ["charge", "charge", "hold", "hold", "self_consume", "self_consume"]
    )
    with pytest.raises(ValueError, match="DST"):
        schedule_from_decisions(
            decisions,
            slots,
            Telemetry(start.isoformat(), 50, 17.2, 2, 0, 2, 0),
            Settings(),
        )
