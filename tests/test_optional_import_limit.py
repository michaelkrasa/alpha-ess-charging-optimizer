"""No invented household import cap unless explicitly configured."""

from dataclasses import replace

import numpy as np
import pytest

from src.manager.daily import score_candidates, plan_daily
from src.manager.settings import Settings
from src.manager.planner import optimize
from tests.test_daily import forecast, battery


def test_absent_import_limit_is_not_a_hidden_20kw_default():
    assert Settings().import_limit_kw is None


def test_no_limit_keeps_profitable_charge_despite_historical_load_envelope():
    settings = Settings(import_limit_kw=None, charge_kw=8.6)
    rows = [replace(s, charging_load_ceiling_kw=30) for s in forecast()]
    scores = score_candidates(
        rows, battery(), settings, [((("01:00", "02:00"),), None)]
    )
    assert np.isfinite(scores).all()
    assert plan_daily(rows, battery(), settings)["charge"]["gridCharge"] == 1
    capped = settings.model_copy(update={"import_limit_kw": 20})
    assert np.isinf(
        score_candidates(rows, battery(), capped, [((("01:00", "02:00"),), None)])
    ).all()


def test_periodic_planner_without_limit_can_charge_above_20kw():
    settings = Settings(import_limit_kw=None, charge_kw=8.6)
    rows = [
        replace(s, load_kw=25, solar_kw=0, import_eur_kwh=0.01 if i < 8 else 0.5)
        for i, s in enumerate(forecast())
    ]
    plan = optimize(rows, replace(battery(), soc_percent=10), settings)
    assert any(d.grid_charge_kw > 0 for d in plan[:8])


@pytest.mark.parametrize("limit", [0, -1, float("inf"), float("nan")])
def test_explicit_limit_still_requires_finite_positive_value(limit):
    with pytest.raises(ValueError):
        Settings(import_limit_kw=limit)
