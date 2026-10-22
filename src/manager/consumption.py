"""Separate likely grid-only EV sessions from battery-demand training.

The AlphaESS charger channel is not connected for this household. Inferred
sessions are explicitly labelled; their full site demand remains in billing.
Completed-day labels may use the end of a session, never future forecast data.
"""

import math
from datetime import datetime

from .domain import PRAGUE

EV_ESTIMATOR_VERSION = "grid-only-step-v1"


def household_training_history(history, asof):
    """Separate grid-only load using only observations available at issue time.

    Keep unmodified history for site import headroom and measured telemetry.
    Process all days together so sessions crossing midnight retain context.
    """
    rows = []
    days = {day for day, _ in history}
    for _, samples in history:
        for r in samples or []:
            try:
                t = datetime.fromisoformat(str(r.get("uploadTime") or r.get("time")))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=PRAGUE)
                if t < asof:
                    rows.append(r)
            except (ValueError, TypeError):
                continue
    cleaned = separate_grid_only_load(rows)
    grouped = {d: [] for d in days}
    for r in cleaned:
        t = datetime.fromisoformat(str(r.get("uploadTime") or r.get("time")))
        if t.tzinfo is None:
            t = t.replace(tzinfo=PRAGUE)
        day = t.astimezone(PRAGUE).date()
        if day in grouped:
            grouped[day].append(r)
    return [(day, grouped[day]) for day, _ in history]


def separate_grid_only_load(rows, *, strict_idle=False):
    """Return timestamped copies with site load, training estimate and EV flag.

    Evidence: a sustained 5.5–12 kW load step, no battery discharge from
    measured site/PV/grid balance, and SOC that does not fall. Charging the
    battery concurrently is permitted. The stricter idle-only interpretation
    is retained for uncertainty checks. This deliberately
    does not label every large appliance as EV, nor treat an unconnected zero
    charger channel as proof that the EV is absent.
    """
    points = []
    for row in rows or []:
        try:
            t = datetime.fromisoformat(str(row.get("uploadTime") or row.get("time")))
            if t.tzinfo is None:
                t = t.replace(tzinfo=PRAGUE)
            load, pv, grid, soc = (
                float(row[k]) for k in ("load", "ppv", "gridCharge", "cbat")
            )
            export = float(row.get("feedIn") or 0)
            if (
                not all(math.isfinite(x) for x in (load, pv, grid, export, soc))
                or min(load, pv, grid, export) < 0
            ):
                continue
        except (ValueError, TypeError, KeyError):
            continue
        points.append(
            {
                "row": dict(row),
                "time": t,
                "load": load / 1000,
                "battery": (load - pv - grid + export) / 1000,
                "soc": soc,
            }
        )
    points.sort(key=lambda p: p["time"])
    result = [
        {
            **p["row"],
            "site_load": p["row"]["load"],
            "ev_grid_only": False,
            "household_load_estimated": False,
        }
        for p in points
    ]
    active = []
    baseline = None

    def finish():
        nonlocal active, baseline
        if len(active) >= 3:
            segment = [points[i] for i in active]
            duration = (
                segment[-1]["time"] - segment[0]["time"]
            ).total_seconds() / 60 + 5
            idle = sum(
                (abs(p["battery"]) if strict_idle else p["battery"]) <= 0.6
                for p in segment
            ) / len(segment)
            swing = (
                max(p["soc"] for p in segment) if strict_idle else segment[0]["soc"]
            ) - min(p["soc"] for p in segment)
            if 15 <= duration <= 600 and idle >= 0.8 and swing <= 2:
                for i in active:
                    result[i].update(
                        load=baseline * 1000,
                        ev_grid_only=True,
                        household_load_estimated=True,
                        ev_estimated_w=max(0, points[i]["load"] - baseline) * 1000,
                    )
        active = []
        baseline = None

    for i, p in enumerate(points):
        past = [
            x
            for x in points[max(0, i - 6) : i]
            if 0 < (p["time"] - x["time"]).total_seconds() <= 1800
        ]
        base = min((x["load"] for x in past), default=p["load"])
        idle = (abs(p["battery"]) if strict_idle else p["battery"]) <= 0.6
        if active:
            if (
                (p["time"] - points[active[-1]]["time"]).total_seconds() > 900
                or p["load"] - baseline < 4.5
                or not idle
            ):
                finish()
            else:
                active.append(i)
                continue
        if past and 5.5 <= p["load"] - base <= 12 and p["load"] >= 6 and idle:
            active = [i]
            baseline = base
    finish()
    return result
