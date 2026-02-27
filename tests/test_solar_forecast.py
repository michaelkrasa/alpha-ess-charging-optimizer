from datetime import date, datetime
from unittest.mock import AsyncMock

import pytest
from zoneinfo import ZoneInfo

from src.optimizer import ESSOptimizer
from src.solar_data import AlphaESSSolarPoint, SolarConfig


class DummyForecaster:
    def __init__(self, points):
        self._points = points

    async def forecast_power(self, *_args, **_kwargs):
        return self._points

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_hourly_forecast_expands_to_quarters():
    """Hourly PV points should be expanded into 4x 15-min slots."""
    tz = ZoneInfo("Europe/Prague")
    points = [
        AlphaESSSolarPoint(timestamp=datetime(2026, 2, 26, 10, 0, tzinfo=tz), pv_power_kw=4.0, raw={}),
        AlphaESSSolarPoint(timestamp=datetime(2026, 2, 26, 11, 0, tzinfo=tz), pv_power_kw=4.0, raw={}),
    ]

    optimizer = ESSOptimizer()
    optimizer.solar_config = SolarConfig(
        latitude=49.0,
        longitude=14.0,
        timezone="Europe/Prague",
        granularity="hourly",
        pv_forecast_enabled=True,
    )
    optimizer.solar_forecaster = DummyForecaster(points)

    slot_kwh = await optimizer._get_pv_forecast_kwh_by_slot(date(2026, 2, 26))

    assert slot_kwh is not None
    # 2 hours * 4 kW = 8 kWh total
    assert pytest.approx(sum(slot_kwh.values()), rel=1e-6) == 8.0
    # Should occupy 8 slots (4 per hour)
    assert len(slot_kwh) == 8


def test_pv_conservative_factor_zero():
    """pv_forecast_conservative_factor=0 must be respected."""
    config = SolarConfig(
        latitude=49.0,
        longitude=14.0,
        timezone="Europe/Prague",
        pv_forecast_conservative_factor=0.0,
    )

    pv_factor = 1.0
    if config.pv_forecast_conservative_factor is not None:
        pv_factor = float(config.pv_forecast_conservative_factor)

    assert pv_factor == 0.0
