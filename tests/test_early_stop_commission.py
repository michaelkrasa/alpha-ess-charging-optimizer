"""Bounded physical-test planning and independent evidence requirements."""

from datetime import datetime, timedelta

import pytest

from src.manager.commission_early_stop import (
    physical_evidence,
    recorded_test_matches,
    relevant_measurements,
)
from src.manager.commission_early_stop import (
    test_schedule as make_test_schedule,
)
from src.manager.domain import PRAGUE
from tests.test_charge_policy import settings


def schedules():
    c = {
        "gridCharge": 1,
        "batHighCap": 100,
        "timeChaf1": "13:15",
        "timeChae1": "14:00",
        "timeChaf2": "15:00",
        "timeChae2": "15:15",
    }
    d = {
        "ctrDis": 1,
        "batUseCap": 10,
        "timeDisf1": "16:30",
        "timeDise1": "20:00",
        "timeDisf2": "21:00",
        "timeDise2": "21:15",
    }
    return c, d


def test_test_does_not_interrupt_current_charge_and_retains_later_charge():
    now = datetime(2026, 10, 23, 13, 30, tzinfo=PRAGUE)
    c, d = schedules()
    out, intent = make_test_schedule(c, d, now)
    assert (
        datetime.fromisoformat(intent["start"]).astimezone(PRAGUE).strftime("%H:%M")
        == "14:00"
    )
    assert out["timeChaf1"] == "13:15" and out["timeChae1"] == "14:15"
    assert out["timeChaf2"] == "15:00"
    assert datetime.fromisoformat(intent["stop_at"]) - datetime.fromisoformat(
        intent["start"]
    ) == timedelta(minutes=5)


def test_household_supply_overlap_is_rejected_before_any_test():
    c, d = schedules()
    d.update(timeDisf1="14:00", timeDise1="14:15")
    with pytest.raises(ValueError, match="overlap"):
        make_test_schedule(c, d, datetime(2026, 10, 23, 13, 30, tzinfo=PRAGUE))


def test_full_soc_or_natural_window_expiry_is_not_proof_of_early_stop():
    start = datetime(2026, 10, 23, 14, tzinfo=PRAGUE)
    intent = {
        "start": start.isoformat(),
        "stop_at": (start + timedelta(minutes=5)).isoformat(),
        "end": (start + timedelta(minutes=15)).isoformat(),
        "confirmed_at": (start + timedelta(minutes=6)).isoformat(),
    }

    def row(minutes, power):
        return {
            "timestamp": (start + timedelta(minutes=minutes)).isoformat(),
            "grid_charge_estimate_kw": power,
        }

    assert not physical_evidence([row(9, 0), row(14, 0)], intent, settings())
    assert physical_evidence([row(4, 8.4), row(9, 0), row(14, 0.1)], intent, settings())
    assert not physical_evidence([row(4, 8.4), row(9, 0)], intent, settings())


def test_measurements_after_natural_end_or_an_excessively_late_command_fail():
    start = datetime(2026, 10, 23, 14, tzinfo=PRAGUE)
    intent = {
        "start": start.isoformat(),
        "stop_at": (start + timedelta(minutes=5)).isoformat(),
        "end": (start + timedelta(minutes=15)).isoformat(),
        "confirmed_at": (start + timedelta(minutes=6)).isoformat(),
    }
    rows = [
        {
            "timestamp": (start + timedelta(minutes=m)).isoformat(),
            "grid_charge_estimate_kw": p,
        }
        for m, p in [(4, 8.4), (19, 0), (24, 0)]
    ]
    assert not physical_evidence(rows, intent, settings())
    intent["confirmed_at"] = (start + timedelta(minutes=9)).isoformat()
    assert not physical_evidence(rows, intent, settings())


def test_five_minute_samples_prove_stop_before_natural_expiry():
    start = datetime(2026, 10, 23, 16, tzinfo=PRAGUE)
    intent = {
        "start": start.isoformat(),
        "stop_at": (start + timedelta(minutes=5)).isoformat(),
        "end": (start + timedelta(minutes=15)).isoformat(),
        "confirmed_at": (start + timedelta(minutes=5, seconds=34)).isoformat(),
    }
    rows = [
        {
            "timestamp": (start + timedelta(minutes=m)).isoformat(),
            "grid_charge_estimate_kw": power,
        }
        for m, power in [(0, 0), (5, 8.5), (10, 0), (15, 0)]
    ]
    assert physical_evidence(rows, intent, settings())
    # Two readings at/after natural expiry cannot prove an early stop.
    assert not physical_evidence([rows[1], rows[3]], intent, settings())
    # A charging sample at/after the confirmed stop cannot prove prior charging.
    intent["confirmed_at"] = (start + timedelta(minutes=5)).isoformat()
    assert not physical_evidence(rows, intent, settings())
    intent["confirmed_at"] = (start + timedelta(minutes=9)).isoformat()
    assert not physical_evidence(rows, intent, settings())


def test_full_soc_and_restarted_charging_cannot_pass_boundary_evidence():
    start = datetime(2026, 10, 23, 16, tzinfo=PRAGUE)
    intent = {
        "start": start.isoformat(),
        "stop_at": (start + timedelta(minutes=5)).isoformat(),
        "confirmed_at": (start + timedelta(minutes=6)).isoformat(),
        "end": (start + timedelta(minutes=15)).isoformat(),
    }
    rows = [
        {
            "timestamp": (start + timedelta(minutes=m)).isoformat(),
            "grid_charge_estimate_kw": p,
            "soc_percent": 60,
        }
        for m, p in [(5, 8.5), (10, 0), (15, 0)]
    ]
    assert physical_evidence(rows, intent, settings())
    rows[-1]["soc_percent"] = 100
    assert not physical_evidence(rows, intent, settings())
    rows[-1]["soc_percent"] = 60
    rows.append(
        {
            "timestamp": (start + timedelta(minutes=12)).isoformat(),
            "grid_charge_estimate_kw": 8.5,
            "soc_percent": 60,
        }
    )
    assert not physical_evidence(rows, intent, settings())


def test_boundary_sample_is_collected_but_later_samples_are_excluded():
    start = datetime(2026, 10, 23, 16, tzinfo=PRAGUE)
    intent = {
        "start": start.isoformat(),
        "end": (start + timedelta(minutes=15)).isoformat(),
    }
    raw = [
        {
            "uploadTime": (start + timedelta(minutes=m)).isoformat(),
            "load": 1000,
            "ppv": 0,
            "gridCharge": 1000,
            "feedIn": 0,
            "cbat": 60,
        }
        for m in (10, 15, 20)
    ]
    assert len(relevant_measurements(raw, intent)) == 2


def test_recorded_recheck_requires_recent_matching_completed_stop_and_on_intent():
    start = datetime(2026, 10, 23, 16, tzinfo=PRAGUE)
    control = {"enabled": True, "revision": "same"}
    intent = {
        "start": start.isoformat(),
        "stop_at": (start + timedelta(minutes=5)).isoformat(),
        "end": (start + timedelta(minutes=15)).isoformat(),
        "confirmed_at": (start + timedelta(minutes=6)).isoformat(),
        "status": "completed",
        "commissioning": True,
        "revision": "same",
    }
    previous = {"owner_authorized": True, "status": "failed", "intent": intent}
    now = start + timedelta(minutes=30)
    assert recorded_test_matches(previous, [intent], control, now)
    assert not recorded_test_matches(previous, [], control, now)
    assert not recorded_test_matches(
        previous, [intent], {**control, "enabled": False}, now
    )
    assert not recorded_test_matches(
        previous, [intent], {**control, "revision": "new"}, now
    )
    assert not recorded_test_matches(
        previous, [intent], control, start + timedelta(hours=2)
    )
    assert not recorded_test_matches(
        previous, [{**intent, "status": "pending"}], control, now
    )


@pytest.mark.parametrize("charging_after_stop", [False, True])
@pytest.mark.parametrize("missing_report_intent", [False, True])
def test_recorded_recheck_reads_device_history_without_another_charge_write(
    tmp_path, monkeypatch, charging_after_stop, missing_report_intent
):
    import asyncio
    from types import SimpleNamespace

    from src.manager import commission_early_stop as module
    from src.manager.store import SQLiteStore

    start = datetime(2026, 10, 23, 16, tzinfo=PRAGUE)
    now = start + timedelta(minutes=30)
    intent = {
        "start": start.isoformat(),
        "stop_at": (start + timedelta(minutes=5)).isoformat(),
        "end": (start + timedelta(minutes=15)).isoformat(),
        "confirmed_at": (start + timedelta(minutes=5, seconds=34)).isoformat(),
        "status": "completed",
        "commissioning": True,
        "revision": "same",
    }
    store = SQLiteStore(tmp_path / "state.sqlite3")
    store.put("control", {"enabled": True, "revision": "same"})
    store.put("early_stops", [intent])
    store.put(
        "early_stop_commission",
        {
            "status": "failed",
            "owner_authorized": True,
            "intent": intent,
        },
    )
    if missing_report_intent:
        previous = store.get("early_stop_commission")
        previous.pop("intent")
        store.put("early_stop_commission", previous)
    raw = [
        {
            "uploadTime": (start + timedelta(minutes=m)).isoformat(),
            "load": 1000,
            "ppv": 0,
            "gridCharge": 1000 + power,
            "feedIn": 0,
            "cbat": 60,
        }
        for m, power in [
            (0, 0),
            (5, 8500),
            (10, 8500 if charging_after_stop else 0),
            (15, 0),
        ]
    ]

    class ReadOnlyDevice:
        def __init__(self, *args):
            pass

        async def power_history(self, day):
            return raw

        async def read_config(self):
            c, d = schedules()
            c.update(
                gridCharge=0,
                timeChaf1="00:00",
                timeChae1="00:00",
                timeChaf2="00:00",
                timeChae2="00:00",
            )
            return c, d

        async def close(self):
            pass

    async def observe(*args):
        return SimpleNamespace(soc_percent=60)

    monkeypatch.setattr(module, "LegacyDevice", ReadOnlyDevice)
    monkeypatch.setattr(module.Controller, "observe", observe)
    monkeypatch.setattr(module, "utc_now", lambda: now)
    monkeypatch.setattr(
        module, "credentials", lambda: ("unused", "unused", "synthetic")
    )
    s = settings(device_control_verified=True)
    if charging_after_stop:
        with pytest.raises(ValueError, match="independent power/readback"):
            asyncio.run(module.commission(s, store))
        assert store.get("early_stop_commission")["status"] == "failed"
        assert store.get("early_stop_commission")["intent"] == intent
        assert not store.get("early_stop_commission")["verification_checks"][
            "physical_power"
        ]
    else:
        result = asyncio.run(module.commission(s, store))
        assert result["status"] == "passed"
        assert result["physical_execution_verified"]
        assert result["recorded_evidence_rechecked"]
        assert len(result["measurements"]) == 4
    assert store.get("early_stops") == [intent]
    assert store.get("lease") is None
