"""The price chart must retain morning peaks and separate market/billed prices."""

from datetime import datetime

from fastapi.testclient import TestClient

from src.manager.domain import PRAGUE, UTC
from src.manager.settings import Settings
from src.manager.store import SQLiteStore
from src.manager.web import create_app
from src.manager.tariff import prices


def test_dashboard_exposes_full_market_day_separately_from_future_plan(
    tmp_path, monkeypatch
):
    now = datetime(2026, 10, 21, 12, tzinfo=PRAGUE).astimezone(UTC)
    monkeypatch.setattr("src.manager.web.utc_now", lambda: now)
    store = SQLiteStore(tmp_path / "state.db")
    spot = [100.0] * 96
    spot[32] = 300.0  # The morning peak must remain visible at noon.
    store.put("prices:ote:2026-10-21", spot)
    store.put(
        "eur_conversion",
        {"rate": 25.0, "published": "2026-10-20", "fetched_day": "2026-10-21"},
    )
    settings = Settings(high_tariff_periods=[])
    client = TestClient(create_app(settings, store))
    state = client.get("/api/state").json()
    rows = state["price_intervals"]
    assert len(rows) == 96
    assert datetime.fromisoformat(rows[0]["timestamp"]).astimezone(PRAGUE).hour == 0
    assert rows[32]["spot_eur_mwh"] == 300.0
    assert rows[32]["import_eur_kwh"] == prices(300, 25, False)[0]
    assert rows[0]["elapsed"] and not rows[60]["elapsed"]
    assert state["plan"] is None  # Prices do not require or fabricate a plan.


def test_incomplete_prices_and_missing_fx_are_not_fabricated(tmp_path, monkeypatch):
    now = datetime(2026, 10, 21, 12, tzinfo=PRAGUE).astimezone(UTC)
    monkeypatch.setattr("src.manager.web.utc_now", lambda: now)
    store = SQLiteStore(tmp_path / "state.db")
    store.put("prices:ote:2026-10-21", [100.0] * 95)
    client = TestClient(create_app(Settings(), store))
    assert client.get("/api/state").json()["price_intervals"] == []
    store.put("prices:ote:2026-10-21", [100.0] * 96)
    rows = client.get("/api/state").json()["price_intervals"]
    assert len(rows) == 96
    assert rows[0]["spot_eur_mwh"] == 100
    assert rows[0]["import_eur_kwh"] is None
