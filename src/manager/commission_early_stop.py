"""Explicit, bounded on-inverter commissioning under the existing writer lease.

Run only with the owner's authorization, inside the deployed service container.
This module never activates policy settings. A caller may activate them only
when the persisted physical evidence reports a passed commissioning result.
"""

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from uuid import uuid4

from .controller import Controller, safe_error
from .device import DeviceUnavailable
from .domain import PRAGUE, UTC, next_boundary, utc_now
from .legacy_device import LegacyDevice
from .partial_charge import mask_stopped_charge
from .settings import credentials, load_settings
from .store import make_store
from .tariff import schedule_verified


def test_schedule(charge, discharge, now):
    """Extend a current charge into the test; retain subsequent charge windows."""
    local = now.astimezone(PRAGUE)
    start = next_boundary(now + timedelta(minutes=3)).astimezone(PRAGUE)
    current = []
    future = []
    if charge["gridCharge"]:
        for j in (1, 2):
            a, b = charge[f"timeChaf{j}"], charge[f"timeChae{j}"]
            if a <= local.strftime("%H:%M") < b:
                start = max(
                    start,
                    datetime.combine(
                        local.date(), datetime.strptime(b, "%H:%M").time(), PRAGUE
                    ),
                )
                current.append((a, b))
            elif a >= local.strftime("%H:%M") and a < b:
                future.append((a, b))
    if start.date() != local.date() or start.strftime("%H:%M") >= "23:30":
        raise ValueError("No representable daytime commissioning quarter remains")
    end = start + timedelta(minutes=15)
    if discharge["ctrDis"]:
        for j in (1, 2):
            a, b = discharge[f"timeDisf{j}"], discharge[f"timeDise{j}"]
            if a < end.strftime("%H:%M") and start.strftime("%H:%M") < b:
                raise ValueError(
                    "Test would overlap an existing household-supply window"
                )
    periods = sorted(
        current + [(start.strftime("%H:%M"), end.strftime("%H:%M"))] + future
    )
    merged = []
    for a, b in periods:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    if len(merged) > 2:
        raise ValueError(
            "Test cannot preserve existing windows within the two-window limit"
        )
    out = {**charge, "gridCharge": 1}
    for j in (1, 2):
        out[f"timeChaf{j}"], out[f"timeChae{j}"] = (
            merged[j - 1] if len(merged) >= j else ("00:00", "00:00")
        )
    LegacyDevice.validate_settings(out, "charge")
    intent = {
        "start": start.astimezone(UTC).isoformat(),
        "stop_at": (start + timedelta(minutes=5)).astimezone(UTC).isoformat(),
        "end": end.astimezone(UTC).isoformat(),
        "status": "pending",
    }
    return out, intent


def relevant_measurements(raw, intent):
    start = datetime.fromisoformat(intent["start"])
    end = datetime.fromisoformat(intent["end"])
    rows = {}
    for r in raw or []:
        try:
            t = datetime.fromisoformat(str(r.get("uploadTime") or r["time"]))
            if t.tzinfo is None:
                t = t.replace(tzinfo=PRAGUE)
            if not start <= t <= end:
                continue
            load, pv, grid, feed = (
                float(r[k]) / 1000 for k in ("load", "ppv", "gridCharge", "feedIn")
            )
            rows[t.isoformat()] = {
                "timestamp": t.astimezone(UTC).isoformat(),
                "soc_percent": float(r["cbat"]),
                "load_kw": load,
                "solar_kw": pv,
                "grid_kw": grid - feed,
                "battery_kw": load - pv - grid + feed,
                "grid_charge_estimate_kw": max(0.0, grid - feed - max(0.0, load - pv)),
            }
        except (ValueError, KeyError, TypeError):
            continue
    return sorted(rows.values(), key=lambda r: r["timestamp"])


def physical_evidence(rows, intent, settings):
    """Require measured charging, an early confirmed stop, and sustained cessation."""
    if not intent.get("confirmed_at"):
        return False
    start, requested, stop, end = (
        datetime.fromisoformat(intent[k])
        for k in ("start", "stop_at", "confirmed_at", "end")
    )
    if not start < stop < end or stop > requested + timedelta(seconds=90):
        return False
    points = sorted((datetime.fromisoformat(r["timestamp"]), r) for r in rows)
    charging = any(
        start + timedelta(seconds=30) <= t < stop
        and r["grid_charge_estimate_kw"] >= settings.charge_kw * 0.5
        for t, r in points
    )
    stopped = [
        t
        for t, r in points
        if stop + timedelta(seconds=30) <= t <= end
        and r["grid_charge_estimate_kw"] <= 0.5
    ]
    if (
        not charging
        or len(stopped) < 2
        or stopped[0] > end - timedelta(minutes=2)
        or stopped[-1] - stopped[0] < timedelta(minutes=2)
    ):
        return False
    return not any(
        r.get("soc_percent", 0) >= 99.9
        or (stopped[0] <= t <= end and r["grid_charge_estimate_kw"] > 0.5)
        for t, r in points
    )


def _recorded_test(previous, intents, control, now):
    """Find one recoverable test in the durable journal, never a new certificate."""
    candidates = (
        [previous]
        if previous.get("intent")
        else [{**previous, "intent": intent} for intent in reversed(intents)]
    )
    return next(
        (r for r in candidates if recorded_test_matches(r, intents, control, now)), None
    )


async def _verify_test(device, record, rows, settings, guard, *, rechecked=False):
    """One certification path for new tests and recovered, freshly read evidence."""
    intent = record["intent"]
    charge, _ = await device.read_config()
    checks = {
        "lease_and_control": guard(),
        "physical_power": physical_evidence(rows, intent, settings),
        "charge_readback": mask_stopped_charge(charge, intent) == charge,
    }
    record.update(
        measurements=rows,
        verification_checks=checks,
        retained_future_windows=charge,
        last_checked_at=utc_now().isoformat(),
    )
    if not all(checks.values()):
        raise ValueError(
            "Recorded stop did not pass independent power/readback verification"
        )
    record.update(
        status="passed",
        completed_at=utc_now().isoformat(),
        recorded_evidence_rechecked=rechecked,
        device_fingerprint=hashlib.sha256(credentials()[2].encode()).hexdigest(),
        charge_kw=settings.charge_kw,
        schedule_verified=True,
        physical_execution_verified=True,
    )
    record.pop("message", None)


def recorded_test_matches(previous, intents, control, now):
    """Only recheck a recent completed stop under the same owner's ON intent."""
    try:
        intent = previous["intent"]
        confirmed = datetime.fromisoformat(intent["confirmed_at"])
        end = datetime.fromisoformat(intent["end"])
        return bool(
            previous.get("owner_authorized")
            and previous.get("status") in ("running", "failed")
            and intent.get("commissioning")
            and intent.get("status") == "completed"
            and control["enabled"]
            and intent["revision"] == control["revision"]
            and end <= now <= confirmed + timedelta(hours=1)
            and any(
                all(
                    r.get(k) == intent.get(k)
                    for k in (
                        "start",
                        "stop_at",
                        "end",
                        "confirmed_at",
                        "revision",
                        "status",
                    )
                )
                for r in intents
            )
        )
    except (KeyError, TypeError, ValueError):
        return False


async def commission(settings, store):
    if not settings.device_control_verified or settings.control_mode != "legacy_daily":
        raise ValueError("Existing standard-window hardware commissioning is required")
    controller = Controller(settings, store, live=True)
    control = controller.control()
    if not control["enabled"]:
        raise ValueError("Automatic management is OFF; commissioning cancelled")
    # Temporary test context is not a persisted verification certificate.
    trial = type(settings).model_validate(
        {**settings.model_dump(), "charge_step_minutes": 5, "early_stop_verified": True}
    )
    stopper = Controller(trial, store, live=True)
    owner = uuid4().hex
    store.acquire(owner, ttl=3600)
    device = None
    original = None
    intent = None
    record = {
        "status": "running",
        "started_at": utc_now().isoformat(),
        "owner_authorized": True,
        "physical_execution_verified": False,
        "schedule_verified": False,
    }

    def guard():
        return (
            store.is_owner(owner, min_remaining=90)
            and controller.control()["enabled"]
            and controller.control()["revision"] == control["revision"]
        )

    try:
        previous = _recorded_test(
            store.get("early_stop_commission", {}),
            store.get("early_stops", []),
            control,
            utc_now(),
        )
        record = {**(previous or {}), **record}
        store.put("early_stop_commission", record)
        device = LegacyDevice(store, settings.legacy_min_write_interval_seconds)
        telemetry = await controller.observe(owner, device)
        if previous:
            raw = await device.power_history(
                datetime.fromisoformat(record["intent"]["start"])
                .astimezone(PRAGUE)
                .date()
            )
            await _verify_test(
                device,
                record,
                relevant_measurements(raw, record["intent"]),
                settings,
                guard,
                rechecked=True,
            )
            store.put("early_stop_commission", record)
            print(json.dumps(record), flush=True)
            return record
        if (
            settings.import_limit_kw is not None
            and max(0.0, telemetry.load_kw - telemetry.solar_kw) + settings.charge_kw
            > settings.import_limit_kw
        ):
            raise ValueError("The test would exceed the configured import limit")
        charge, discharge = await device.read_config()
        device.require_daily_baseline(discharge, settings.reserve_percent)
        proposed, intent = test_schedule(charge, discharge, utc_now())
        if not schedule_verified(datetime.fromisoformat(intent["start"]), settings):
            raise ValueError("Test tariff schedule is not verified")
        capacity = settings.effective_capacity_kwh or telemetry.capacity_kwh
        until_start = (
            datetime.fromisoformat(intent["start"]) - utc_now()
        ).total_seconds() / 3600
        currently_charging = charge["gridCharge"] and any(
            a <= utc_now().astimezone(PRAGUE).strftime("%H:%M") < b
            for a, b in [
                (charge[f"timeChaf{j}"], charge[f"timeChae{j}"]) for j in (1, 2)
            ]
        )
        needed = (
            settings.charge_kw
            * settings.charge_efficiency
            * (0.25 + (until_start if currently_charging else 0))
        )
        if capacity * (1 - telemetry.soc_percent / 100) < needed + 0.2:
            raise ValueError(
                "Insufficient headroom to distinguish an early stop from natural full-SOC saturation"
            )
        original = (charge, discharge)
        intent.update(revision=control["revision"], commissioning=True)
        # Persist the deadline before any new charge setting can reach the inverter.
        existing = [
            r
            for r in store.get("early_stops", [])
            if datetime.fromisoformat(r["end"]) <= utc_now()
        ]
        store.put("early_stops", [*existing, intent])
        await device.replace_pair(proposed, discharge, charge, discharge, guard)
        record.update(
            intent=intent,
            charge_before=charge,
            charge_test=proposed,
            discharge=discharge,
        )
        store.put("early_stop_commission", record)
        print(
            json.dumps(
                {
                    "phase": "armed",
                    "start": intent["start"],
                    "stop_at": intent["stop_at"],
                    "natural_end": intent["end"],
                }
            ),
            flush=True,
        )
        last_read = None
        last_notice = None
        timeout = datetime.fromisoformat(intent["end"]) + timedelta(minutes=8)
        while utc_now() < timeout:
            if not guard():
                raise DeviceUnavailable(
                    "Control revision or commissioning lease changed"
                )
            now = utc_now()
            if now >= datetime.fromisoformat(intent["stop_at"]):
                await stopper.stop_charge_once(lease_owner=owner)
                intent = next(
                    r
                    for r in store.get("early_stops", [])
                    if r["start"] == intent["start"]
                )
            # Leave a runway for the timer write; historical samples collected
            # after stopping prove charging during the preceding five minutes.
            runway = (
                0
                < (
                    datetime.fromisoformat(intent["stop_at"]) - utc_now()
                ).total_seconds()
                < 120
            )
            if not runway and (
                last_read is None or (utc_now() - last_read).total_seconds() >= 60
            ):
                telemetry = await controller.observe(owner, device)
                raw = await device.power_history(utc_now().astimezone(PRAGUE).date())
                rows = relevant_measurements(raw, intent)
                record.update(
                    intent=intent,
                    measurements=rows,
                    last_checked_at=utc_now().isoformat(),
                )
                store.put("early_stop_commission", record)
                last_read = utc_now()
                if intent.get("status") == "completed" and physical_evidence(
                    rows, intent, settings
                ):
                    await _verify_test(device, record, rows, settings, guard)
                    store.put("early_stop_commission", record)
                    print(json.dumps(record), flush=True)
                    return record
            if last_notice is None or (utc_now() - last_notice).total_seconds() >= 60:
                print(
                    json.dumps(
                        {
                            "phase": "verifying"
                            if intent.get("status") == "completed"
                            else "waiting",
                            "now": utc_now().isoformat(),
                            "stop_at": intent["stop_at"],
                        }
                    ),
                    flush=True,
                )
                last_notice = utc_now()
            await asyncio.sleep(5)
        raise ValueError(
            "Insufficient measured charging/stopping evidence before the test timeout"
        )
    except Exception as exc:
        record.update(
            status="failed", message=safe_error(exc), completed_at=utc_now().isoformat()
        )
        store.put("early_stop_commission", record)
        raise
    finally:
        try:
            if original and device and guard():
                # Preserve the owner's previous future windows without restarting
                # an already stopped test quarter. The full lease prevents a race.
                current, native = await device.read_config()
                restored = original[0]
                if intent and utc_now() >= datetime.fromisoformat(intent["start"]):
                    restored = mask_stopped_charge(restored, intent)
                await device.replace_pair(restored, original[1], current, native, guard)
        except Exception as cleanup_error:
            record.update(
                status="failed",
                message="Commissioning cleanup failed: " + safe_error(cleanup_error),
            )
            store.put("early_stop_commission", record)
            raise
        finally:
            try:
                if device:
                    await device.close()
            finally:
                store.release(owner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="manager.yaml")
    parser.add_argument("--owner-authorized", action="store_true", required=True)
    args = parser.parse_args()
    settings = load_settings(args.config)
    asyncio.run(commission(settings, make_store(settings)))


if __name__ == "__main__":
    main()
