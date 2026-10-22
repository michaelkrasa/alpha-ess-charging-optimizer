"""Timing sensitivity delays decisions without advancing information visibility."""

from datetime import date, datetime, timedelta

import numpy as np
import pytest

from analysis import backtest_household
from analysis.backtest_household import (
    Day,
    Exchange,
    Forecaster,
    daily_decision_due,
    evaluate,
    make_slots,
    validate_daily_decision_minute,
)
from src.manager.domain import PRAGUE, UTC
from src.manager.settings import Settings


def test_replay_ignores_auxiliary_commissioning_diagnostics(tmp_path, monkeypatch):
    output = tmp_path / "analysis/output"
    output.mkdir(parents=True)
    (output / "alphaess_power_2025.csv").write_text("timestamp,load_kw\n")
    (output / "ote_prices_2025.csv").write_text("timestamp,price_eur_per_mwh\n")
    raw = tmp_path / "data/backtests"
    (raw / "household").mkdir(parents=True)
    (raw / "household/2026-10-20.full.json").write_text("[]")
    monkeypatch.setattr(backtest_household, "ROOT", tmp_path)
    monkeypatch.setattr(backtest_household, "RAW", raw)
    assert backtest_household.load_history() == ({}, {})


@pytest.mark.parametrize("minute", [899, 1440, -1, True, 915.5, "915"])
def test_timing_validation_rejects_prepublication_or_invalid_minutes(minute):
    with pytest.raises(ValueError, match="900 to 1439"):
        validate_daily_decision_minute(minute)
    with pytest.raises(ValueError, match="900 to 1439"):
        evaluate(
            {},
            None,
            Settings(),
            date(2026, 10, 1),
            date(2026, 10, 2),
            daily_decision_minute=minute,
        )


@pytest.mark.parametrize("minute", [900, 915, 960, 1439])
def test_timing_eligibility_uses_local_clock_and_keeps_24h_budget(minute):
    now = datetime(2026, 10, 21, minute // 60, minute % 60, tzinfo=PRAGUE).astimezone(
        UTC
    )
    assert daily_decision_due(now, None, minute)
    assert not daily_decision_due(now - timedelta(minutes=1), None, minute)
    assert daily_decision_due(now, now - timedelta(hours=24), minute)
    assert not daily_decision_due(now, now - timedelta(hours=24, seconds=-1), minute)


def test_default_timing_stays_at_15_local():
    assert daily_decision_due(datetime(2026, 10, 21, 15, tzinfo=PRAGUE), None)
    assert not daily_decision_due(datetime(2026, 10, 21, 14, 59, tzinfo=PRAGUE), None)


def test_delayed_decisions_do_not_reveal_prices_before_publication():
    today = date(2026, 10, 21)

    def day(d):
        return Day(
            d,
            np.ones(96),
            np.zeros(96),
            np.ones(96),
            np.zeros(96),
            [[1]] * 96,
            [[0]] * 96,
            np.full(96, 100.0),
        )

    days = {
        today - timedelta(days=i): day(today - timedelta(days=i)) for i in range(-1, 8)
    }
    forecaster = Forecaster(days, "persistence")
    rates = Exchange({today - timedelta(days=1): 25.0, today: 25.0})
    before = datetime(2026, 10, 21, 14, 45, tzinfo=PRAGUE).astimezone(UTC)
    delayed = datetime(2026, 10, 21, 16, tzinfo=PRAGUE).astimezone(UTC)
    assert not daily_decision_due(before, None, 960)
    assert daily_decision_due(delayed, None, 960)
    before_slots = make_slots(before, days, forecaster, rates, "nt")
    delayed_slots = make_slots(delayed, days, forecaster, rates, "nt")
    assert (
        max(
            datetime.fromisoformat(s.timestamp).astimezone(PRAGUE).date()
            for s in before_slots
        )
        == today
    )
    assert max(
        datetime.fromisoformat(s.timestamp).astimezone(PRAGUE).date()
        for s in delayed_slots
    ) == today + timedelta(days=1)


def test_standard_rolling_replay_uses_live_planner_contract_and_updated_soc(
    monkeypatch,
):
    from src.manager.daily import payloads

    today = date(2026, 10, 21)
    days = {}
    for offset in range(-7, 2):
        d = today + timedelta(days=offset)
        days[d] = Day(
            d,
            np.ones(96),
            np.zeros(96),
            np.ones(96),
            np.zeros(96),
            [[1]] * 96,
            [[0]] * 96,
            np.full(96, 100.0),
        )
    settings = Settings()
    charge, discharge = payloads((("00:15", "02:00"),), None, settings)
    calls = []

    def planner(slots, state, settings, **kwargs):
        calls.append((state.soc_percent, kwargs, slots[0].timestamp))
        return {
            "charge": charge,
            "discharge": discharge,
            "valid_from": slots[0].timestamp,
        }

    monkeypatch.setattr("src.manager.daily.plan_daily", planner)
    summary, _ = evaluate(
        days,
        Exchange({today - timedelta(days=1): 25.0}),
        settings,
        today,
        today,
        policies=["standard_daily", "standard_rolling"],
    )
    rolling = [c for c in calls if c[1].get("minimum_slots") == 4]
    daily = [c for c in calls if c[1].get("minimum_slots") != 4]
    assert len(rolling) == 96 and len(daily) == 1
    assert rolling[0][1]["current_schedule"] is None
    assert rolling[1][1]["current_schedule"]["charge"] == charge
    assert len({round(c[0], 2) for c in rolling}) > 2
    assert summary["standard_diagnostics"]["standard_rolling"]["schedule_changes"] == 1
    assert not summary["standard_diagnostics"]["standard_rolling"]["failures"]


def test_archived_pv_cloudy_fallback_cannot_renew_old_calibration(monkeypatch):
    today = date(2026, 10, 21)
    calibration_day = today - timedelta(days=7)
    forecaster = Forecaster({}, "weather")

    def calibrate(day):
        if day == calibration_day:
            return 8.0
        raise ValueError("Insufficient causal PV calibration samples")

    monkeypatch.setattr(forecaster, "_scale_from_previous_day", calibrate)
    assert forecaster.scale(today) == 8.0
    assert forecaster.scale_source[today] == calibration_day
    with pytest.raises(ValueError, match="within seven days"):
        forecaster.scale(today + timedelta(days=1))


def test_once_daily_ablation_does_not_replan_when_schedule_is_unchanged(monkeypatch):
    from src.manager.daily import payloads
    from tests.test_backtest import sample_day

    today = date(2026, 10, 21)
    days = {
        today + timedelta(days=i): sample_day(today + timedelta(days=i))
        for i in range(-7, 4)
    }
    settings = Settings()
    charge, discharge = payloads((), None, settings)
    calls = []

    def planner(rows, *args, **kwargs):
        assert kwargs["minimum_slots"] == 4
        calls.append(rows[0].timestamp)
        return {
            "charge": charge,
            "discharge": discharge,
            "valid_from": rows[0].timestamp,
        }

    monkeypatch.setattr("src.manager.daily.plan_daily", planner)
    summary, _ = evaluate(
        days,
        Exchange({today - timedelta(days=1): 25}),
        settings,
        today,
        today + timedelta(days=2),
        policies=["standard_once_daily"],
    )
    assert len(calls) == 3
    assert all(
        datetime.fromisoformat(t).astimezone(PRAGUE).strftime("%H:%M") == "15:15"
        for t in calls
    )
    assert (
        summary["standard_diagnostics"]["standard_once_daily"]["schedule_changes"] == 1
    )


def test_rolling_replay_restores_native_after_forecast_failure_grace(monkeypatch):
    from src.manager import daily
    from tests.test_backtest import sample_day

    today = date(2026, 10, 21)
    days = {
        today + timedelta(days=i): sample_day(today + timedelta(days=i))
        for i in range(-7, 2)
    }
    settings = Settings()
    charge, discharge = daily.payloads((), (), settings)
    real_slots = backtest_household.make_slots

    def slots(now, *args):
        if now.astimezone(PRAGUE).hour or now.astimezone(PRAGUE).minute >= 30:
            raise ValueError("Forecast unavailable")
        return real_slots(now, *args)

    def planner(rows, *args, **kwargs):
        return {
            "charge": charge,
            "discharge": discharge,
            "valid_from": rows[0].timestamp,
        }

    allowed = []
    real_step = daily.step

    def step(*args, **kwargs):
        allowed.append(kwargs["discharge"])
        return real_step(*args, **kwargs)

    monkeypatch.setattr(backtest_household, "make_slots", slots)
    monkeypatch.setattr(daily, "plan_daily", planner)
    monkeypatch.setattr(daily, "step", step)
    evaluate(
        days,
        Exchange({today - timedelta(days=1): 25}),
        settings,
        today,
        today,
        policies=["standard_rolling"],
    )
    assert allowed[:5] == [True, False, False, False, True]
