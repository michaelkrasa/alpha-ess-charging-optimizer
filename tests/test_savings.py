"""Savings compare measured grid cost with the same house and solar without a battery."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.manager.domain import PRAGUE, day_slots
from src.manager.energy_day import parse_power_history
from src.manager.savings import dashboard_savings, day_savings, needs_final, record_day
from src.manager.store import SQLiteStore

DAY = date(2026, 10, 21)
SETTINGS = SimpleNamespace(price_source="ote", high_tariff_periods=None, tariff_valid_from=None)


def price(t, buy, sell=0.05):
    return {"timestamp": t.isoformat(), "import_eur_kwh": buy, "export_eur_kwh": sell}


def sample(t, load, solar, grid):
    return {"timestamp": t.isoformat(), "load_kw": load, "solar_kw": solar, "grid_kw": grid}


def test_charging_cheap_and_supplying_dear_saves_the_price_spread_less_losses():
    q = day_slots(DAY)
    now = q[3] + timedelta(minutes=15)
    prices = [price(q[0], 0.10), price(q[1], 0.40), price(q[2], 0.40), price(q[3], 0.40)]
    rows = [
        # Quarter 0: house 1 kW, battery charges 4 kW from the grid.
        sample(q[0], 1, 0, 5), sample(q[0] + timedelta(minutes=5), 1, 0, 5),
        # Quarter 1: battery supplies the whole 3.6 kW house.
        sample(q[1], 3.6, 0, 0),
        # Quarter 2: no battery activity: no saving.
        sample(q[2], 2, 0, 2),
        # Quarter 3: solar surplus stored instead of exported: a small cost now.
        sample(q[3], 1, 3, 0),
    ]
    result = day_savings(rows, prices, now)
    expected = -4 * 0.10 / 4 + 3.6 * 0.40 / 4 + 0 - 2 * 0.05 / 4
    assert result["saved_eur"] == pytest.approx(expected)
    assert result["cost_without_battery_eur"] == pytest.approx((0.10 + 3.6 * 0.40 + 0.80 - 2 * 0.05) / 4)
    assert result["quarters"] == 4


def test_unmeasured_unpriced_and_unfinished_quarters_are_skipped():
    q = day_slots(DAY)
    now = q[2] + timedelta(minutes=7)
    prices = [price(q[0], None), price(q[1], 0.3), price(q[2], 0.3)]
    rows = [sample(q[0], 3, 0, 0), {"timestamp": q[1].isoformat(), "load_kw": 3, "solar_kw": 0}, sample(q[2], 3, 0, 0)]
    assert day_savings(rows, prices, now)["quarters"] == 0


def test_cloud_history_grid_is_import_minus_export():
    t = datetime(2026, 10, 21, 12, tzinfo=PRAGUE)
    rows = parse_power_history([{"uploadTime": t.isoformat(), "load": 1000, "ppv": 3000, "cbat": 50,
                                 "gridCharge": 0, "feedIn": 500}])
    assert rows[0]["grid_kw"] == -0.5
    assert "grid_kw" not in parse_power_history([{"uploadTime": t.isoformat(), "load": 1, "ppv": 0}])[0]


def test_ledger_keeps_running_today_final_yesterday_and_total(tmp_path):
    store = SQLiteStore(tmp_path / "s.db")
    store.put("eur_conversion", {"rate": 25.0, "published": "2026-10-21"})
    for day in (DAY, DAY + timedelta(days=1)):
        store.put(f"prices:ote:{day}", [100.0] * len(day_slots(day)))
    q = day_slots(DAY)
    supply = [sample(t, 2, 0, 0) for t in q]
    now = datetime(2026, 10, 22, 0, 5, tzinfo=PRAGUE)
    assert needs_final(store, now) == DAY
    record_day(store, SETTINGS, DAY, supply, now, final=True)
    assert needs_final(store, now) is None
    record_day(store, SETTINGS, DAY, [], now)  # a final day is never recomputed
    today = DAY + timedelta(days=1)
    t = day_slots(today)
    record_day(store, SETTINGS, today, [sample(t[0], 2, 0, 0)], t[1])
    view = dashboard_savings(store, t[1])
    assert view["since"] == "2026-10-21"
    assert view["yesterday"]["quarters"] == 96 and view["yesterday"]["final"]
    assert view["today"]["quarters"] == 1
    assert view["total_eur"] == pytest.approx(view["yesterday"]["saved_eur"] + view["today"]["saved_eur"], abs=0.01)
    assert view["yesterday"]["saved_eur"] > 0
    assert dashboard_savings(SQLiteStore(tmp_path / "empty.db"), now) is None
