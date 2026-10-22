"""Daily measured graph uses Prague midnight, not a rolling event window."""

from datetime import date, datetime, timedelta

import pytest

from src.manager.domain import PRAGUE, day_slots
from src.manager.energy_day import (
    cache_power_history,
    dashboard_energy,
    record_measurement,
)
from src.manager.store import SQLiteStore


def row(t, load=2):
    return {"timestamp": t.isoformat(), "load_kw": load, "solar_kw": 1}


def test_daily_series_sorts_deduplicates_and_omits_other_days_and_future(tmp_path):
    store = SQLiteStore(tmp_path / "day.db")
    now = datetime(2026, 10, 21, 12, tzinfo=PRAGUE)
    earlier = now - timedelta(hours=2)
    for t in [now, earlier, earlier, now - timedelta(days=1), now + timedelta(hours=1)]:
        store.append({"telemetry": row(t)})
    record_measurement(store, row(now), now)
    result = dashboard_energy(store, now, store.history())
    assert len(result["rows"]) == 2
    assert result["rows"][0]["timestamp"] < result["rows"][1]["timestamp"]
    assert datetime.fromisoformat(result["start"]).astimezone(PRAGUE).hour == 0
    assert result["ticks"][-1]["label"] == "24:00"
    tomorrow = now.replace(hour=0) + timedelta(days=1)
    reset = dashboard_energy(store, tomorrow, store.history())
    assert reset["rows"] == []
    record_measurement(store, row(tomorrow), tomorrow)
    assert len(store.get("energy_day")["rows"]) == 1


@pytest.mark.parametrize(
    "day,hours", [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25)]
)
def test_daily_axis_handles_prague_daylight_saving_days(tmp_path, day, hours):
    store = SQLiteStore(tmp_path / "dst.db")
    now = day_slots(day)[-1]
    result = dashboard_energy(store, now, [])
    assert (
        datetime.fromisoformat(result["end"]) - datetime.fromisoformat(result["start"])
    ).total_seconds() == hours * 3600
    assert [t["label"] for t in result["ticks"]] == [
        "00:00",
        "06:00",
        "12:00",
        "18:00",
        "24:00",
    ]


def test_cloud_history_backfills_since_midnight_without_losing_live_samples(tmp_path):
    store = SQLiteStore(tmp_path / "backfill.db")
    now = datetime(2026, 10, 21, 12, tzinfo=PRAGUE)
    record_measurement(store, row(now, load=3), now)
    cache_power_history(
        store,
        [
            {"uploadTime": "2026-10-21 00:03:00", "load": 1200, "ppv": 0},
            {"uploadTime": "2026-10-20 23:58:00", "load": 9000, "ppv": 0},
            {"uploadTime": "2026-10-21 13:00:00", "load": 9000, "ppv": 0},
        ],
        now,
    )
    rows = dashboard_energy(store, now, [])["rows"]
    assert len(rows) == 2
    assert rows[0]["load_kw"] == 1.2
    assert rows[-1]["load_kw"] == 3
    checked = store.get("energy_day")["history_checked_at"]
    record_measurement(
        store, row(now + timedelta(minutes=1)), now + timedelta(minutes=1)
    )
    assert store.get("energy_day")["history_checked_at"] == checked
