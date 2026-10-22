from dataclasses import replace
from datetime import datetime, date, timedelta
import json

from fastapi.testclient import TestClient
import pytest

from src.manager.controller import Controller
from src.manager.device import AlphaDevice, DeviceUnavailable
from src.manager.domain import UTC, PRAGUE, Slot, Telemetry, day_slots, utc_now
from src.manager.planner import optimize
from src.manager.providers import parse_entsoe
from src.manager.settings import Settings
from src.manager.store import SQLiteStore, BusyError
from src.manager.tariff import prices, high_tariff
from src.manager.web import create_app


def slots(costs, load=1, pv=0):
    start = datetime(2026, 10, 20, tzinfo=UTC)
    return [
        Slot(
            (start + timedelta(minutes=15 * i)).isoformat(),
            cost * 1000,
            cost,
            cost - 0.02,
            load,
            pv,
            "NT",
        )
        for i, cost in enumerate(costs)
    ]


def battery(soc=10, capacity=10):
    return Telemetry(utc_now().isoformat(), soc, capacity, 1, 0, 1, 0)


def test_daylight_saving_intervals_are_actual_instants():
    assert len(day_slots(date(2026, 3, 29))) == 92
    assert len(day_slots(date(2026, 10, 25))) == 100
    assert len(day_slots(date(2026, 10, 20))) == 96
    assert len({t.timestamp() for t in day_slots(date(2026, 10, 25))}) == 100


def test_invoice_marginal_prices_include_vat_but_not_standing_charges():
    low, sell = prices(100, 25, False)
    high, _ = prices(100, 25, True)
    assert low == pytest.approx(
        (100 + (390 + 116.5 + 164.24 + 28.3) / 25) * 1.21 / 1000
    )
    assert high - low == pytest.approx((754.77 - 116.5) / 25 * 1.21 / 1000)
    assert sell == pytest.approx((100 - 390 / 25) / 1000)
    assert prices(-100, 25, False)[0] < 0


def test_distribution_schedule_can_cross_midnight():
    assert high_tariff(datetime(2026, 10, 20, 23, tzinfo=PRAGUE), [["22:00", "02:00"]])
    assert not high_tariff(
        datetime(2026, 10, 20, 12, tzinfo=PRAGUE), [["22:00", "02:00"]]
    )


def test_optimizer_buys_cheap_energy_for_later_household_demand():
    plan = optimize(
        slots([0.01] * 4 + [0.4] * 8), battery(), Settings(wear_eur_per_kwh=0.01)
    )
    assert sum(p.grid_charge_kw for p in plan[:4]) > 0
    assert sum(p.battery_load_kw for p in plan[4:]) > 0
    assert min(p.soc_percent for p in plan) >= 10 - 1e-6
    assert max(p.soc_percent for p in plan) <= 100 + 1e-6


def test_flat_price_does_not_cycle_battery_for_no_benefit():
    plan = optimize(slots([0.1] * 16), battery(), Settings())
    assert sum(p.grid_charge_kw for p in plan) == pytest.approx(0)


def test_no_battery_export_even_when_spot_export_price_is_high():
    inputs = [replace(s, export_eur_kwh=10) for s in slots([0.1] * 8, load=0)]
    plan = optimize(inputs, battery(100), Settings())
    assert all(p.battery_load_kw == 0 for p in plan)
    assert all(p.grid_charge_kw == 0 for p in plan)


def test_negative_prices_cannot_create_simultaneous_battery_flows():
    plan = optimize(slots([-0.5] * 12), battery(50), Settings())
    assert any(p.grid_charge_kw > 0 for p in plan)
    assert all(not (p.grid_charge_kw > 0 and p.battery_load_kw > 0) for p in plan)


def test_grid_limit_and_no_grid_charging_during_solar_surplus():
    settings = Settings(import_limit_kw=2)
    plan = optimize(slots([-0.5] * 8, load=1.8), battery(), settings)
    assert max(p.grid_charge_kw for p in plan) <= 0.201
    solar = optimize(slots([-0.5] * 8, load=1, pv=4), battery(), settings)
    assert all(p.grid_charge_kw == 0 for p in solar)


def test_planner_rejects_missing_price_intervals_and_nan():
    values = slots([0.1] * 8)
    with pytest.raises(ValueError, match="contiguous"):
        optimize(values[:2] + values[3:], battery(), Settings())
    with pytest.raises(ValueError, match="Non-finite"):
        optimize([replace(values[0], load_kw=float("nan"))], battery(), Settings())


def test_stale_telemetry_is_rejected():
    old = replace(battery(), timestamp=(utc_now() - timedelta(minutes=25)).isoformat())
    with pytest.raises(ValueError, match="stale"):
        old.validate(utc_now(), 20)


def test_entsoe_parser_expands_hourly_prices_and_requires_eur():
    times = day_slots(date(2026, 10, 20))[:4]
    xml = (
        "<Publication><TimeSeries><currency_Unit.name>EUR</currency_Unit.name><price_Measure_Unit.name>MWH</price_Measure_Unit.name><Period><timeInterval><start>"
        + times[0].isoformat()
        + "</start></timeInterval><resolution>PT60M</resolution><Point><position>1</position><price.amount>-20</price.amount></Point></Period></TimeSeries></Publication>"
    )
    assert parse_entsoe(xml, times) == [-20] * 4
    with pytest.raises(ValueError, match="EUR"):
        parse_entsoe(xml.replace("EUR", "USD"), times)
    with pytest.raises(ValueError, match="Incomplete"):
        parse_entsoe(xml, times + [times[-1] + timedelta(minutes=15)])


def test_sqlite_history_and_lease_persist_across_process_instances(tmp_path):
    a = SQLiteStore(tmp_path / "manager.db")
    b = SQLiteStore(tmp_path / "manager.db")
    a.put("control", {"enabled": False})
    a.append({"type": "test"})
    assert b.get("control") == {"enabled": False}
    assert b.history() == [{"type": "test"}]
    a.acquire("a")
    with pytest.raises(BusyError):
        b.acquire("b")
    b.release("b")
    with pytest.raises(BusyError):
        b.acquire("b")
    a.release("a")
    b.acquire("b")
    b.release("b")


class FakeDevice:
    native_calls = 0
    fail_native = False
    closed = False

    async def native(self, reserve, verified, **kwargs):
        self.native_calls += 1
        if self.fail_native:
            raise DeviceUnavailable("API unavailable")
        return {"status": "accepted", "periods_verified": True}

    async def telemetry(self):
        return battery()

    async def read_config(self):
        return {}, {"batUseCap": 10}

    async def apply(self, *args):
        raise AssertionError("No unverified writes allowed")

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_off_is_durable_and_pending_if_api_is_unavailable(tmp_path):
    store = SQLiteStore(tmp_path / "db")
    device = FakeDevice()
    device.fail_native = True
    controller = Controller(Settings(), store, live=True, device_factory=lambda: device)
    store.put("control", {"enabled": True, "revision": 1})
    await controller.set_enabled(False)
    assert store.get("control")["enabled"] is False
    assert store.get("control")["restore_pending"] is True
    assert store.get("actuation")["status"] == "pending"
    assert device.closed
    device.fail_native = False
    await controller.set_enabled(False)
    assert store.get("control")["restore_pending"] is False


@pytest.mark.asyncio
async def test_no_dispatch_without_verified_device_and_complete_tariff(tmp_path):
    store = SQLiteStore(tmp_path / "db")
    device = FakeDevice()
    controller = Controller(Settings(), store, live=True, device_factory=lambda: device)
    store.put("control", {"enabled": True, "restore_pending": False, "revision": 1})

    async def data(*args):
        return slots([0.1] * 8), []

    controller.providers.slots = data
    result = await controller.run_once()
    assert result["status"] == "blocked"
    assert device.native_calls == 0
    assert store.get("plan")["decisions"]


@pytest.mark.asyncio
async def test_stale_device_data_never_dispatches_a_plan(tmp_path):
    store = SQLiteStore(tmp_path / "db")
    device = FakeDevice()

    async def telemetry():
        return replace(
            battery(), timestamp=(utc_now() - timedelta(hours=1)).isoformat()
        )

    device.telemetry = telemetry
    controller = Controller(
        Settings(), store, live=False, device_factory=lambda: device
    )
    result = await controller.run_once()
    assert result["status"] == "degraded"
    assert store.get("plan") is None


def test_dashboard_requires_auth_and_protects_controls(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    settings = Settings(database_path=str(tmp_path / "db"))
    store = SQLiteStore(settings.database_path)
    controller = Controller(settings, store, live=False, device_factory=FakeDevice)

    async def forecast(*args):
        return slots([0.1] * 8), []

    controller.providers.slots = forecast
    client = TestClient(create_app(settings, store, controller=controller, require_auth=True))
    assert client.get("/api/state").status_code == 401
    assert "Household password" in client.get("/").text
    auth = ("household", "test-password")
    response = client.get("/", auth=auth)
    assert (
        response.status_code == 200 and "no-store" in response.headers["cache-control"]
    )
    assert "10% reserve" in response.text
    assert (
        client.post("/api/control", auth=auth, json={"enabled": False}).status_code
        == 403
    )
    headers = {"X-Control-Request": "dashboard"}
    assert (
        client.post(
            "/api/control",
            auth=auth,
            headers={**headers, "Origin": "https://evil.example"},
            json={"enabled": False},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/control", auth=auth, headers=headers, json={"enabled": "false"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/control", auth=auth, headers=headers, json={"enabled": False}
        ).status_code
        == 200
    )
    assert client.get("/api/state", auth=auth).json()["control"]["enabled"] is False


def test_periods_must_be_nonempty_aligned_and_explicitly_power_limited():
    period = {
        "beginTime": "01:00",
        "endTime": "02:00",
        "chargeLimit": 10,
        "chargePower": 100,
    }
    assert AlphaDevice.valid_periods([period])
    assert not AlphaDevice.valid_periods([])
    for changes in (
        {"chargePower": 0},
        {"chargePower": 0.5},
        {"endTime": "24:00"},
        {"beginTime": "01:02"},
        {"chargeLimit": 5},
    ):
        assert not AlphaDevice.valid_periods([{**period, **changes}])


@pytest.mark.asyncio
async def test_off_is_saved_immediately_during_a_running_cycle(tmp_path):
    store = SQLiteStore(tmp_path / "db")
    store.put("control", {"enabled": True, "revision": 1, "restore_pending": False})
    store.acquire("long-running-cycle")
    controller = Controller(Settings(), store, live=True, device_factory=FakeDevice)
    result = await controller.set_enabled(False)
    assert result["enabled"] is False and result["restore_pending"] is True
    assert store.get("control")["enabled"] is False
    store.release("long-running-cycle")


@pytest.mark.asyncio
async def test_off_during_forecasting_prevents_subsequent_dispatch(tmp_path):
    store = SQLiteStore(tmp_path / "db")
    store.put("control", {"enabled": True, "revision": 1, "restore_pending": False})
    settings = Settings(
        high_tariff_periods=[], hold_verified=True, device_control_verified=True
    )
    device = FakeDevice()
    controller = Controller(settings, store, live=True, device_factory=lambda: device)

    async def forecast(*args):
        await controller.set_enabled(False)
        return slots([0.1] * 8), []

    controller.providers.slots = forecast
    result = await controller.run_once()
    assert result["status"] == "observing"
    assert not store.get("control")["enabled"]


def test_sign_in_uses_http_only_session_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    settings = Settings(database_path=str(tmp_path / "db"))
    store = SQLiteStore(settings.database_path)
    client = TestClient(create_app(settings, store, require_auth=True))
    headers = {"X-Control-Request": "dashboard"}
    assert (
        client.post(
            "/api/login", headers=headers, json={"password": "wrong"}
        ).status_code
        == 401
    )
    response = client.post(
        "/api/login", headers=headers, json={"password": "test-password"}
    )
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert client.get("/api/state").status_code == 200
    assert "Automatic management" in client.get("/").text


@pytest.mark.asyncio
async def test_untimestamped_snapshot_uses_a_timestamped_historical_sample():
    device = object.__new__(AlphaDevice)
    device.serial = "test-inverter"
    stamp = utc_now().astimezone(PRAGUE).replace(microsecond=0)

    async def call(name, *args, **kwargs):
        if name == "getESSList":
            return [{"sysSn": "test-inverter", "cobat": 17.2, "minv": "T10"}]
        if name == "getLastPowerData":
            return {"soc": 99, "pload": 99999}
        if name == "getOneDayPowerBySn":
            return [
                {
                    "uploadTime": stamp.replace(tzinfo=None).isoformat(),
                    "cbat": 46,
                    "load": 1100,
                    "ppv": 500,
                    "gridCharge": 400,
                    "feedIn": 0,
                }
            ]
        raise AssertionError(name)

    device.call = call
    result = await device.telemetry()
    result.validate(utc_now(), 20)
    assert result.soc_percent == 46
    assert result.load_kw == 1.1
    assert result.capacity_kwh == 17.2


def test_native_restore_cannot_claim_success_with_legacy_schedules_active():
    AlphaDevice.require_native_baseline(
        {"batUseCap": 10, "ctrDis": 0, "legacy_grid_charge": 0}, 10
    )
    with pytest.raises(DeviceUnavailable, match="Legacy schedules"):
        AlphaDevice.require_native_baseline(
            {"batUseCap": 10, "ctrDis": 1, "legacy_grid_charge": 1}, 10
        )
