"""Local access and truthful, independently read inverter schedule feedback."""

from fastapi.testclient import TestClient
from src.manager.settings import Settings
from src.manager.device import DeviceUnavailable
from src.manager.legacy_device import LegacyDevice
from src.manager.store import SQLiteStore
from src.manager.web import create_app
import pytest
from tests.test_pair_transition import Device, charge, discharge


def test_local_dashboard_needs_no_credentials_and_keeps_control_origin_checks(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    store = SQLiteStore(tmp_path / "state.db")
    c = TestClient(create_app(Settings(), store))
    assert c.get("/").status_code == 200
    assert "Household password" not in c.get("/").text
    assert c.get("/api/state").status_code == 200
    assert not c.get("/api/state").json()["authentication_required"]
    assert c.post("/api/control", json={"enabled": True}).status_code == 403
    assert (
        c.post(
            "/api/control",
            headers={
                "X-Control-Request": "dashboard",
                "Origin": "https://unrelated.example",
            },
            json={"enabled": True},
        ).status_code
        == 403
    )


def test_cloud_cannot_accidentally_disable_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "test-only")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-only")
    c = TestClient(
        create_app(Settings(), SQLiteStore(tmp_path / "cloud.db"), require_auth=False)
    )
    assert c.get("/api/state").status_code == 401
    assert c.get("/api/state", auth=("household", "test-only")).status_code == 200


async def test_partial_write_remains_unconfirmed_and_successful_pair_is_read_back(
    tmp_path,
):
    device = Device(tmp_path)
    device.fail_second = True
    with pytest.raises(DeviceUnavailable, match="unconfirmed"):
        await device.replace(charge("02:00", "04:00"), discharge("10:00", "12:00"))
    assert device.store.get("device_schedule")["readback_pending"]
    # Fresh state and independent device fake for the successful path.
    other = tmp_path / "other"
    other.mkdir()
    device = Device(other)
    await device.replace(charge("02:00", "04:00"), discharge("10:00", "12:00"))
    snapshot = device.store.get("device_schedule")
    assert not snapshot["readback_pending"]
    assert snapshot["charge"] == device.current_charge
    assert snapshot["discharge"] == device.current_discharge
    assert snapshot["source"] == "pair_readback"


async def test_incomplete_inverter_response_cannot_be_presented_as_confirmed(tmp_path):
    device = object.__new__(LegacyDevice)
    device.serial = "test-only"
    device.store = SQLiteStore(tmp_path / "invalid.db")

    async def empty_read(*args, **kwargs):
        return {}

    device.call = empty_read
    with pytest.raises(DeviceUnavailable, match="unavailable"):
        await device.read_config()
    assert device.store.get("device_schedule") is None
