from datetime import datetime, timedelta
import pytest
from src.manager.daily import refresh_daily
from src.manager.domain import UTC, Slot, Telemetry
from src.manager.settings import Settings


def test_saved_schedule_projection_restarts_from_current_measured_soc():
    start = datetime(2026, 10, 21, 8, tzinfo=UTC)
    slots = [
        Slot((start + timedelta(minutes=15 * i)).isoformat(), 200, 0.2, 0.1, 1, 0, "NT")
        for i in range(4)
    ]
    old = {
        "created_at": (start - timedelta(hours=10)).isoformat(),
        "valid_until": slots[2].timestamp,
        "charge": {
            "gridCharge": 0,
            "batHighCap": 100,
            "timeChaf1": "00:00",
            "timeChae1": "00:00",
            "timeChaf2": "00:00",
            "timeChae2": "00:00",
        },
        "discharge": {
            "ctrDis": 0,
            "batUseCap": 10,
            "timeDisf1": "00:00",
            "timeDise1": "00:00",
            "timeDisf2": "00:00",
            "timeDise2": "00:00",
        },
        "decisions": [{"soc_percent": 90}],
        "notes": [],
        "forecast_benefit_eur": 1,
        "forecast_improvement_vs_legacy_eur": 2,
    }
    result = refresh_daily(
        old, slots, Telemetry(start.isoformat(), 20, 17.2, 1, 0, 1, 0), Settings()
    )
    assert result["charge"] == old["charge"]
    assert result["discharge"] == old["discharge"]
    assert len(result["decisions"]) == 2
    assert len(result["outlook_decisions"]) == 4
    assert all(10 <= x["soc_percent"] < 20 for x in result["decisions"])
    assert old["decisions"][0]["soc_percent"] == 90
    assert result["forecast_benefit_eur"] is None
    assert result["forecast_improvement_vs_legacy_eur"] is None


def test_cached_projection_cannot_outlive_schedule():
    stamp = datetime(2026, 10, 21, 8, tzinfo=UTC).isoformat()
    with pytest.raises(ValueError, match="expired"):
        refresh_daily(
            {"valid_until": stamp}, [Slot(stamp, 0, 0, 0, 1, 0, "NT")], None, Settings()
        )
