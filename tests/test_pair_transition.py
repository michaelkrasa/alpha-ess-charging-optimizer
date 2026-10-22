"""A daily pair remains non-overlapping if its second endpoint fails."""

import pytest

from src.manager.device import DeviceUnavailable
from src.manager.domain import utc_now
from src.manager.legacy_device import LegacyDevice
from src.manager.store import SQLiteStore


def charge(start="00:00", end="02:00", enabled=1):
    return dict(
        gridCharge=enabled,
        batHighCap=100,
        timeChaf1=start,
        timeChae1=end,
        timeChaf2="00:00",
        timeChae2="00:00",
    )


def discharge(start="06:00", end="08:00", enabled=1):
    return dict(
        ctrDis=enabled,
        batUseCap=10,
        timeDisf1=start,
        timeDise1=end,
        timeDisf2="00:00",
        timeDise2="00:00",
    )


class Device(LegacyDevice):
    """Exercise real validation, ordering, budgets and readback without a network."""

    def __init__(self, tmp_path):
        self.store = SQLiteStore(tmp_path / "transition.sqlite")
        self.serial = "test-only"
        self.current_charge = charge()
        self.current_discharge = discharge()
        self.writes = []
        self.fail_second = False

    async def call(self, method, *args, **kwargs):
        if method.startswith("update"):
            assert kwargs.pop("_write_guard")()
            kind = "charge" if method == "updateChargeConfigInfo" else "discharge"
            self.writes.append(kind)
            if self.fail_second and len(self.writes) == 2:
                raise DeviceUnavailable("Injected second-endpoint failure")
            setattr(self, "current_" + kind, kwargs.copy())
        if method == "getChargeConfigInfo":
            return self.current_charge.copy()
        if method == "getDisChargeConfigInfo":
            return self.current_discharge.copy()

    async def replace(self, c, d):
        return await self.replace_pair(
            c,
            d,
            self.current_charge.copy(),
            self.current_discharge.copy(),
            lambda: True,
        )


async def test_charge_first_when_new_discharge_overlaps_old_charge(tmp_path):
    device = Device(tmp_path)
    await device.replace(charge("10:00", "12:00"), discharge("00:00", "02:00"))
    assert device.writes == ["charge", "discharge"]


async def test_discharge_first_when_new_charge_overlaps_old_discharge(tmp_path):
    device = Device(tmp_path)
    await device.replace(charge("06:00", "08:00"), discharge("10:00", "12:00"))
    assert device.writes == ["discharge", "charge"]


async def test_preserve_discharge_first_when_both_transitions_safe(tmp_path):
    device = Device(tmp_path)
    await device.replace(charge("02:00", "04:00"), discharge("10:00", "12:00"))
    assert device.writes == ["discharge", "charge"]


async def test_window_swap_disables_charge_before_replacing_both_windows(
    tmp_path, monkeypatch
):
    from datetime import timedelta

    device = Device(tmp_path)
    clock = [utc_now()]
    monkeypatch.setattr("src.manager.legacy_device.utc_now", lambda: clock[0])
    original_call = device.call

    async def spaced_call(*args, **kwargs):
        result = await original_call(*args, **kwargs)
        clock[0] += timedelta(seconds=10)
        return result

    device.call = spaced_call
    await device.replace(charge("06:00", "08:00"), discharge("00:00", "02:00"))
    assert device.writes == ["charge", "discharge", "charge"]
    assert device.current_charge == charge("06:00", "08:00")
    assert device.current_discharge == discharge("00:00", "02:00")


async def test_window_swap_failure_leaves_charging_disabled(tmp_path):
    device = Device(tmp_path)
    device.fail_second = True
    with pytest.raises(DeviceUnavailable, match="unconfirmed"):
        await device.replace(charge("06:00", "08:00"), discharge("00:00", "02:00"))
    assert not device.current_charge["gridCharge"]
    assert device.store.get("device_schedule")["readback_pending"]


async def test_disabled_stored_period_does_not_force_reordering(tmp_path):
    device = Device(tmp_path)
    device.current_charge["gridCharge"] = 0
    await device.replace(charge("10:00", "12:00"), discharge("00:00", "02:00"))
    assert device.writes == ["discharge", "charge"]


async def test_unchanged_endpoint_can_keep_its_spent_budget(tmp_path):
    device = Device(tmp_path)
    device.store.put("legacy_charge_write", {"attempted_at": utc_now().isoformat()})
    await device.replace(device.current_charge.copy(), discharge("10:00", "12:00"))
    assert device.writes == ["discharge"]


async def test_off_can_clear_preexisting_overlapping_periods(tmp_path):
    device = Device(tmp_path)
    device.current_discharge = discharge("00:00", "02:00")
    await device.replace(charge(enabled=0), discharge("00:00", "02:00", enabled=0))
    assert device.writes == ["discharge", "charge"]
    assert not device.current_charge["gridCharge"]
    assert not device.current_discharge["ctrDis"]


@pytest.mark.parametrize("charge_first", [False, True])
async def test_second_write_failure_preserves_safe_intermediate(tmp_path, charge_first):
    device = Device(tmp_path)
    device.fail_second = True
    c, d = (
        (charge("10:00", "12:00"), discharge("00:00", "02:00"))
        if charge_first
        else (charge("06:00", "08:00"), discharge("10:00", "12:00"))
    )
    with pytest.raises(DeviceUnavailable, match="endpoints are not atomic"):
        await device.replace(c, d)
    actual_c, actual_d = device.current_charge, device.current_discharge
    assert not (
        actual_c["timeChaf1"] < actual_d["timeDise1"]
        and actual_d["timeDisf1"] < actual_c["timeChae1"]
    )
