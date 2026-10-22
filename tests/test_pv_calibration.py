from datetime import datetime, timedelta
import pytest
from src.manager.providers import Providers
from src.manager.settings import Settings
from src.manager.domain import UTC, Telemetry
from src.manager.store import SQLiteStore
from src.manager.load_profile import LOAD_PROFILE_VERSION


@pytest.mark.asyncio
@pytest.mark.parametrize("age,usable", [(2, True), (7, True), (8, False)])
async def test_overcast_day_uses_bounded_prior_calibration(tmp_path, age, usable):
    now = datetime(2026, 10, 21, 8, tzinfo=UTC)
    store = SQLiteStore(tmp_path / "state.db")
    store.put(
        "load_profile",
        {
            "day": "2026-10-21",
            "estimator": LOAD_PROFILE_VERSION,
            "values": {str(i): 1.0 for i in range(96)},
        },
    )
    store.put(
        "pv_scale",
        {
            "fetched": (now - timedelta(days=age)).isoformat(),
            "kw": 4.0,
            "irradiance_alignment_version": 2,
        },
    )
    store.put(
        "solar_forecast",
        {
            "fetched": now.isoformat(),
            "data": {"time": ["2026-10-21T09:00"], "global_tilted_irradiance": [100]},
        },
    )
    solar_config = tmp_path / "solar.yaml"
    solar_config.write_text("latitude: 50\nlongitude: 14\n")
    settings = Settings(solar_config_path=str(solar_config))

    class Overcast:
        async def power_history(self, day):
            return []

    provider = Providers(settings, store)
    call = provider.forecast(
        [now], Overcast(), Telemetry(now.isoformat(), 50, 17.2, 1, 0, 1, 0), now
    )
    if usable:
        load, solar = await call
        assert solar == pytest.approx([0.32])
        assert (
            store.get("pv_scale")["fetched"] == (now - timedelta(days=age)).isoformat()
        )
    else:
        with pytest.raises(ValueError, match="calibrat"):
            await call
