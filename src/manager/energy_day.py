"""Observed daily power, independent of rolling controller event history."""

import math
from datetime import datetime, timedelta

from .domain import PRAGUE, UTC, day_slots


def cache_daily_totals(store, raw, now):
    day = str(now.astimezone(PRAGUE).date())
    if not isinstance(raw, dict) or str(raw.get('theDate', ''))[:10] != day:
        raise ValueError('Daily energy response does not match the requested local day')
    values = {}
    for source, target in [('epv', 'solar_generated_kwh'), ('eCharge', 'battery_charged_kwh'),
                           ('eGridCharge', 'grid_charged_kwh')]:
        try:
            value = float(raw[source])
        except (KeyError, ValueError, TypeError):
            continue
        if math.isfinite(value) and value >= 0:
            values[target] = value
    store.put('daily_energy', {'day': day, 'checked_at': now.isoformat(), **values})


def dashboard_daily_totals(store, now):
    totals = store.get('daily_energy') or {}
    return totals if totals.get('day') == str(now.astimezone(PRAGUE).date()) else None


def day_measurements(rows, now):
    day = now.astimezone(PRAGUE).date()
    found = {}
    for row in rows:
        try:
            t = datetime.fromisoformat(row["timestamp"])
            load, solar = float(row["load_kw"]), float(row["solar_kw"])
            if (
                t.tzinfo is None
                or t.astimezone(PRAGUE).date() != day
                or t > now
                or not all(math.isfinite(v) and v >= 0 for v in (load, solar))
            ):
                continue
            key = t.astimezone(UTC).isoformat()
            previous_soc = found.get(key, {}).get("soc_percent")
            found[key] = {
                "timestamp": t.astimezone(UTC).isoformat(),
                "load_kw": load,
                "solar_kw": solar,
            }
            soc = row.get("soc_percent")
            if not isinstance(soc, (int, float)) or not math.isfinite(soc) or not 0 <= soc <= 100:
                soc = previous_soc
            if isinstance(soc, (int, float)) and math.isfinite(soc) and 0 <= soc <= 100:
                found[key]["soc_percent"] = float(soc)
        except (ValueError, TypeError, KeyError):
            continue
    return [found[t] for t in sorted(found)]


def record_measurement(store, telemetry, now):
    day = str(now.astimezone(PRAGUE).date())
    cached = store.get("energy_day") or {}
    if cached.get("day") == day:
        rows = cached["rows"]
    else:
        # Seed the first daily bucket from existing observations, then preserve
        # it independently of the rolling history's record limit.
        rows = [r["telemetry"] for r in store.history(672) if r.get("telemetry")]
    store.put(
        "energy_day",
        {
            **(cached if cached.get("day") == day else {}),
            "day": day,
            "rows": day_measurements([*rows, telemetry], now),
        },
    )


def cache_power_history(store, samples, now):
    """Backfill measured samples since midnight; cloud powers are watts."""
    rows = []
    for sample in samples or []:
        try:
            stamp = datetime.fromisoformat(
                str(sample.get("uploadTime") or sample["time"])
            )
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=PRAGUE)
            rows.append(
                {
                    "timestamp": stamp.isoformat(),
                    "load_kw": float(sample["load"]) / 1000,
                    "solar_kw": float(sample["ppv"]) / 1000,
                    "soc_percent": float(sample["cbat"]) if sample.get("cbat") is not None else None,
                }
            )
        except (ValueError, KeyError, TypeError):
            continue
    day = str(now.astimezone(PRAGUE).date())
    cached = store.get("energy_day") or {}
    current = cached.get("rows", []) if cached.get("day") == day else []
    store.put(
        "energy_day",
        {
            "day": day,
            "history_checked_at": now.isoformat(),
            "soc_history_backfilled": True,
            "rows": day_measurements([*rows, *current], now),
        },
    )


def dashboard_energy(store, now, history):
    day = now.astimezone(PRAGUE).date()
    start = day_slots(day)[0]
    end = day_slots(day + timedelta(days=1))[0]
    cached = store.get("energy_day") or {}
    rows = cached.get("rows", []) if cached.get("day") == str(day) else []
    latest = store.get("telemetry")
    rows = [*rows, *(r["telemetry"] for r in history if r.get("telemetry"))]
    if latest:
        rows.append(latest)
    ticks = [
        {"timestamp": t.isoformat(), "label": t.astimezone(PRAGUE).strftime("%H:%M")}
        for t in day_slots(day)
        if t.astimezone(PRAGUE).hour % 6 == 0 and t.astimezone(PRAGUE).minute == 0
    ]
    return {
        "day": str(day),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "ticks": [*ticks, {"timestamp": end.isoformat(), "label": "24:00"}],
        "rows": day_measurements(rows, now),
    }
