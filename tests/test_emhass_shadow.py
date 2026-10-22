"""Shadow must not gain write authority or stop the selected controller."""

from datetime import datetime, timedelta

import pytest

pytest.importorskip("emhass")
from src.manager.controller import Controller
from src.manager.domain import UTC, Slot, Telemetry
from src.manager.emhass_mpc import EmhassPlanner
from src.manager.settings import Settings
from src.manager.store import SQLiteStore


def inputs():
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
    return slots, Telemetry(start.isoformat(), 10, 14.2, 2, 0, 2, 0)


async def test_actual_shadow_uses_same_state_and_does_not_dispatch(tmp_path):
    c = Controller(
        Settings(
            control_mode="legacy_daily",
            emhass_shadow=True,
            charge_kw=8.6,
            discharge_kw=8.6,
        ),
        SQLiteStore(tmp_path / "db"),
        device_factory=lambda: pytest.fail("Shadow created a device"),
    )
    slots, state = inputs()
    selected, comparison = await c.standard_plan(slots, state, None)
    assert selected["planner"] == "candidate"
    assert comparison["status"] == "compared"
    assert not comparison["shadow_controls_device"]
    assert comparison["measured_soc_percent"] == state.soc_percent
    assert c._emhass is not None


async def test_shadow_failure_preserves_candidate_plan(tmp_path, monkeypatch):
    def broken(*args):
        raise ValueError("Synthetic solver unavailable")

    monkeypatch.setattr(EmhassPlanner, "plan", broken)
    c = Controller(
        Settings(control_mode="legacy_daily", emhass_shadow=True),
        SQLiteStore(tmp_path / "db"),
    )
    selected, comparison = await c.standard_plan(*inputs(), None)
    assert selected["planner"] == "candidate"
    assert comparison["status"] == "unavailable"
    assert "Synthetic solver" in comparison["message"]


async def test_selecting_emhass_changes_the_returned_writer_plan(tmp_path):
    c = Controller(
        Settings(
            control_mode="legacy_daily",
            standard_planner="emhass",
            charge_kw=8.6,
            discharge_kw=8.6,
        ),
        SQLiteStore(tmp_path / "db"),
    )
    selected, comparison = await c.standard_plan(*inputs(), None)
    assert selected["planner"] == "emhass"
    assert selected["version"] == 3
    assert comparison["status"] == "planned"
    assert comparison["candidate"] is None


async def test_candidate_shadow_failure_cannot_stop_active_emhass(
    tmp_path, monkeypatch
):
    def broken(*args, **kwargs):
        raise ValueError("Synthetic candidate unavailable")

    monkeypatch.setattr("src.manager.controller.plan_daily", broken)
    c = Controller(
        Settings(
            control_mode="legacy_daily",
            standard_planner="emhass",
            emhass_shadow=True,
            charge_kw=8.6,
            discharge_kw=8.6,
        ),
        SQLiteStore(tmp_path / "db"),
    )
    selected, comparison = await c.standard_plan(*inputs(), None)
    assert selected["planner"] == "emhass"
    assert comparison["status"] == "partial"
    assert "Synthetic candidate" in comparison["candidate_error"]
