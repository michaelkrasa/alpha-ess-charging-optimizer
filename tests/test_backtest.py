from datetime import date, datetime, timedelta
import numpy as np
import pytest

from analysis.backtest_household import (
    Day,
    Exchange,
    Forecaster,
    physical_step,
    make_slots,
    integrate_day,
)
from src.manager.domain import PRAGUE, UTC
from src.manager.settings import Settings


def sample_day(day, load=1, pv=0, spot=100):
    return Day(
        day,
        np.full(96, load),
        np.full(96, pv),
        np.full(96, load),
        np.zeros(96),
        [[load]] * 96,
        [[pv]] * 96,
        np.full(96, spot),
    )


def test_forecast_ignores_future_actual_consumption_and_solar():
    today = date(2025, 1, 8)
    days = {
        today - timedelta(days=i): sample_day(today - timedelta(days=i), load=2, pv=1)
        for i in range(8)
    }
    forecast = Forecaster(days, "persistence")
    now = datetime(2025, 1, 8, 12, tzinfo=PRAGUE).astimezone(UTC)
    instants = [now + timedelta(minutes=15)]
    before = forecast.forecast(now, instants)
    days[today] = sample_day(today, load=1000, pv=500)
    after = Forecaster(days, "persistence").forecast(now, instants)
    assert np.array_equal(before[0], after[0])
    assert np.array_equal(before[1], after[1])


def test_next_day_prices_are_hidden_until_15_local_time():
    today = date(2025, 1, 8)
    days = {
        today - timedelta(days=i): sample_day(today - timedelta(days=i))
        for i in range(-1, 8)
    }
    forecaster = Forecaster(days, "persistence")
    rates = Exchange({date(2025, 1, 6): 25.0, date(2025, 1, 7): 25.0, today: 24.0})
    before = make_slots(
        datetime(2025, 1, 8, 14, 45, tzinfo=PRAGUE).astimezone(UTC),
        days,
        forecaster,
        rates,
        "nt",
    )
    after = make_slots(
        datetime(2025, 1, 8, 15, tzinfo=PRAGUE).astimezone(UTC),
        days,
        forecaster,
        rates,
        "nt",
    )
    assert (
        max(
            datetime.fromisoformat(s.timestamp).astimezone(PRAGUE).date()
            for s in before
        )
        == today
    )
    assert max(
        datetime.fromisoformat(s.timestamp).astimezone(PRAGUE).date() for s in after
    ) == today + timedelta(days=1)


def test_archived_forecasts_have_six_hour_information_lag():
    now = datetime(2025, 6, 1, 11, 59, tzinfo=UTC)
    assert Forecaster.available_run(now) == datetime(2025, 6, 1, 0, tzinfo=UTC)
    assert Forecaster.available_run(now + timedelta(minutes=1)) == datetime(
        2025, 6, 1, 6, tzinfo=UTC
    )


def test_physical_simulation_conserves_energy_and_never_exports_battery():
    settings = Settings()
    initial = 8.6
    flow = physical_step(initial, 2, 0, settings, "native")
    assert flow.grid_export == 0
    assert flow.grid_import == pytest.approx(0)
    assert flow.energy == pytest.approx(
        initial - flow.discharge / settings.discharge_efficiency
    )
    solar = physical_step(initial, 1, 6, settings, "hold", 0.1, 10)
    assert solar.discharge == 0 and solar.grid_charge == 0
    assert solar.energy - initial == pytest.approx(
        solar.pv_charge * settings.charge_efficiency
    )
    assert solar.pv_charge + solar.grid_export == pytest.approx((6 - 1) * 0.25)


def test_saturated_reserve_and_import_limit_are_physical_constraints():
    settings = Settings(import_limit_kw=20)
    drained = physical_step(1.72, 8, 0, settings, "native")
    assert drained.energy == pytest.approx(1.72) and drained.discharge == 0
    charge = physical_step(8.6, 19, 0, settings, "charge", 5, 100)
    assert charge.grid_import * 4 <= 20 + 1e-9


def test_ambiguous_dst_days_are_explicitly_excluded():
    with pytest.raises(ValueError, match="DST"):
        integrate_day(date(2025, 10, 26), [])


@pytest.mark.asyncio
async def test_live_solar_forecast_interprets_preceding_hour_irradiance(tmp_path):
    from src.manager.providers import Providers
    from src.manager.store import SQLiteStore
    from src.manager.domain import Telemetry

    now = datetime(2026, 10, 20, 10, tzinfo=UTC)
    store = SQLiteStore(tmp_path / "db")
    store.put(
        "load_profile",
        {
            "day": "2026-10-20",
            "values": {str(i): 1 for i in range(96)},
            "estimator": "mean-integrated-v2",
        },
    )
    store.put(
        "pv_scale",
        {"fetched": now.isoformat(), "kw": 10, "irradiance_alignment_version": 2},
    )
    store.put(
        "solar_forecast",
        {
            "fetched": now.isoformat(),
            "data": {
                "time": ["2026-10-20T11:00", "2026-10-20T12:00"],
                "global_tilted_irradiance": [100, 200],
            },
        },
    )
    providers = Providers(Settings(), store)
    telemetry = Telemetry(now.isoformat(), 50, 17.2, 1, 0, 0, 0)
    load, solar = await providers.forecast(
        [datetime(2026, 10, 20, 11, 15, tzinfo=UTC)], None, telemetry, now
    )
    assert load == [1]
    assert solar == pytest.approx([1.6])
