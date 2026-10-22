"""Standard API optimization: two charge and two household-discharge windows.

Every candidate is scored using executable native/hold/full-power behavior.
Includes the original policy and overnight full-charge options. Candidate search
is bounded; the selected forecast score cannot be worse than its baseline.
"""

import math
from datetime import datetime, timedelta
from dataclasses import replace
from itertools import pairwise

import numpy as np

from .daily_charge_only import in_window
from .daily_charge_only import step as native_step
from .domain import PRAGUE, Decision
from .legacy_policy import legacy_periods


def step(
    energy,
    load,
    solar,
    charge,
    target,
    capacity,
    settings,
    discharge=True,
    duration_hours=0.25,
):
    if charge or discharge:
        return native_step(
            energy, load, solar, charge, target, capacity, settings, duration_hours
        )
    # Discharge time control preserves energy; PV still charges normally.
    _e, imported, exported, d, _g, p = native_step(
        energy, load, solar, False, target, capacity, settings, duration_hours
    )
    return energy + p * settings.charge_efficiency, imported + d, exported, 0.0, 0.0, p


def refresh_daily(saved, slots, telemetry, settings):
    """Reproject saved controls from fresh SOC while awaiting tomorrow's prices.

    The settings are retained, not reoptimized or dispatched. Yesterday's
    predicted SOC cannot stand in for today's measured battery state.
    """
    horizon = [
        s
        for s in slots
        if datetime.fromisoformat(s.timestamp)
        < datetime.fromisoformat(saved["valid_until"])
    ]
    if not horizon:
        raise ValueError("Cached daily schedule horizon has expired")
    charge, discharge = saved["charge"], saved["discharge"]
    cp = (
        [(charge[f"timeChaf{j}"], charge[f"timeChae{j}"]) for j in (1, 2)]
        if charge["gridCharge"]
        else []
    )
    dp = (
        [(discharge[f"timeDisf{j}"], discharge[f"timeDise{j}"]) for j in (1, 2)]
        if discharge["ctrDis"]
        else None
    )
    _, decisions = simulate(
        slots, telemetry, settings, cp, charge["batHighCap"], True, dp
    )
    notes = [
        n
        for n in saved.get("notes", [])
        if not n.startswith("Saved settings retained;")
    ]
    return {
        **saved,
        "decisions": [d.dict() for d in decisions[: len(horizon)]],
        "outlook_decisions": [d.dict() for d in decisions],
        "forecast_benefit_eur": None,
        "forecast_improvement_vs_legacy_eur": None,
        "notes": [
            *notes,
            "Saved settings retained; remaining projection refreshed from measured SOC. No new economic comparison before full prices are available.",
        ],
    }


def _mask(periods, clocks):
    if periods is None:
        return np.ones(len(clocks), dtype=bool)
    return np.array([any(a <= c < b for a, b in periods) for c in clocks], dtype=bool)


def score_candidates(slots, telemetry, settings, candidates, enforce_headroom=True):
    clocks = [
        datetime.fromisoformat(s.timestamp).astimezone(PRAGUE).strftime("%H:%M")
        for s in slots
    ]
    cm = np.array([_mask(c, clocks) for c, d in candidates])
    dm = np.array([_mask(d, clocks) for c, d in candidates])
    energy = np.full(
        len(candidates), telemetry.capacity_kwh * telemetry.soc_percent / 100
    )
    floor = telemetry.capacity_kwh * settings.reserve_percent / 100
    ceiling = telemetry.capacity_kwh * settings.maximum_percent / 100
    costs = np.zeros(len(candidates))
    valid = np.ones(len(candidates), dtype=bool)
    for i, s in enumerate(slots):
        deficit, surplus = (
            max(0, s.load_kw - s.solar_kw),
            max(0, s.solar_kw - s.load_kw),
        )
        pv = np.maximum(
            0,
            np.minimum(
                min(surplus, settings.charge_kw),
                (ceiling - energy) / (0.25 * settings.charge_efficiency),
            ),
        )
        energy += pv * 0.25 * settings.charge_efficiency
        grid = np.where(
            cm[:, i],
            np.maximum(
                0,
                np.minimum(
                    settings.charge_kw - pv,
                    (ceiling - energy) / (0.25 * settings.charge_efficiency),
                ),
            ),
            0.0,
        )
        discharge = np.where(
            ~cm[:, i] & dm[:, i],
            np.maximum(
                0,
                np.minimum(
                    min(deficit, settings.discharge_kw),
                    (energy - floor) * settings.discharge_efficiency / 0.25,
                ),
            ),
            0.0,
        )
        energy += (
            grid * 0.25 * settings.charge_efficiency
            - discharge * 0.25 / settings.discharge_efficiency
        )
        imports = deficit + grid - discharge
        if enforce_headroom and settings.import_limit_kw is not None:
            valid &= ~((grid > 1e-8) & (imports > settings.import_limit_kw + 1e-7))
        # Cost uses expected energy; safe charging opportunities use a separate
        # recent-load envelope. Do not assume forecast PV will provide headroom.
        if (
            enforce_headroom
            and settings.import_limit_kw is not None
            and s.charging_load_ceiling_kw is not None
            and s.charging_load_ceiling_kw + settings.charge_kw
            > settings.import_limit_kw + 1e-7
        ):
            valid &= ~cm[:, i]
        costs += 0.25 * (
            imports * s.import_eur_kwh
            - (surplus - pv) * s.export_eur_kwh
            + discharge * settings.wear_eur_per_kwh
        )
    terminal = max(
        0,
        min(s.import_eur_kwh for s in slots) * settings.discharge_efficiency
        - settings.wear_eur_per_kwh,
    )
    return np.where(valid, costs - energy * terminal, np.inf)


def simulate(
    slots, telemetry, settings, periods, target, details=False, discharge_periods=None
):
    candidate = (
        tuple(periods),
        None if discharge_periods is None else tuple(discharge_periods),
    )
    score = float(score_candidates(slots, telemetry, settings, [candidate])[0])
    if not details:
        return score, []
    energy = telemetry.capacity_kwh * telemetry.soc_percent / 100
    result = []
    for s in slots:
        t = datetime.fromisoformat(s.timestamp)
        charge = in_window(t, periods)
        allowed = discharge_periods is None or in_window(t, discharge_periods)
        energy, _imp, _exp, d, g, p = step(
            energy,
            s.load_kw,
            s.solar_kw,
            charge,
            target,
            telemetry.capacity_kwh,
            settings,
            allowed,
        )
        action = "charge" if charge else "self_consume" if allowed else "hold"
        result.append(
            Decision(
                s.timestamp,
                action,
                g * 4,
                d * 4,
                p * 4,
                energy / telemetry.capacity_kwh * 100,
                s.import_eur_kwh,
                s.export_eur_kwh,
                s.load_kw,
                s.solar_kw,
                {
                    "charge": "Charge in a selected low-cost window, up to the configured full SOC.",
                    "self_consume": "Use the battery for household demand above reserve.",
                    "hold": "Preserve battery for a higher-value household discharge window; PV capture continues.",
                }[action],
            )
        )
    return score, result


def _windows(slots, lengths, cheap, count=2, blackout=None):
    times = [datetime.fromisoformat(s.timestamp).astimezone(PRAGUE) for s in slots]
    found = set()
    for length in lengths:
        ranked = []
        for i in range(len(slots) - length + 1):
            a = times[i]
            b = (
                datetime.fromisoformat(slots[i + length - 1].timestamp)
                + timedelta(minutes=15)
            ).astimezone(PRAGUE)
            if a.date() != b.date():
                continue
            period = (a.strftime("%H:%M"), b.strftime("%H:%M"))
            if blackout and period[0] <= blackout < period[1]:
                continue
            values = slots[i : i + length]
            if cheap:
                value = sum(s.import_eur_kwh for s in values) / length
            else:
                value = -sum(
                    max(0, s.load_kw - s.solar_kw) * max(0, s.import_eur_kwh)
                    for s in values
                )
            ranked.append((value, period))
        found.update(p for _, p in sorted(ranked)[:count])
    return sorted(found)


def _options(windows):
    return (
        [()]
        + [(p,) for p in windows]
        + [
            (a, b)
            for i, a in enumerate(windows)
            for b in windows[i + 1 :]
            if a[1] <= b[0]
        ]
    )


def payloads(charge, discharge, settings):
    def pad(periods):
        return list(periods or ()) + [("00:00", "00:00")] * (2 - len(periods or ()))

    c, d = pad(charge), pad(discharge)
    return (
        {
            "gridCharge": int(bool(charge)),
            "batHighCap": int(settings.maximum_percent),
            "timeChaf1": c[0][0],
            "timeChae1": c[0][1],
            "timeChaf2": c[1][0],
            "timeChae2": c[1][1],
        },
        {
            "ctrDis": int(discharge is not None),
            "batUseCap": settings.reserve_percent,
            "timeDisf1": d[0][0],
            "timeDise1": d[0][1],
            "timeDisf2": d[1][0],
            "timeDise2": d[1][1],
        },
    )


def validate_standard_horizon(horizon):
    """Standard HH:MM controls cannot distinguish a repeated DST hour."""
    stamps = [datetime.fromisoformat(s.timestamp) for s in horizon]
    if any(
        t.tzinfo is None or t.minute % 15 or t.second or t.microsecond
        for t in stamps
    ) or any(
        (b - a).total_seconds() != 900 for a, b in pairwise(stamps)
    ):
        raise ValueError(
            "Daily horizon must be contiguous timezone-aware quarter hours aligned to price boundaries"
        )
    if (
        len(
            {
                t.astimezone(PRAGUE).utcoffset()
                for t in stamps + [stamps[-1] + timedelta(minutes=15)]
            }
        )
        != 1
    ):
        raise ValueError(
            "Daily wall-clock schedule across a DST change is not commissioned"
        )
    if any(
        not math.isfinite(v)
        for s in horizon
        for v in (s.import_eur_kwh, s.export_eur_kwh, s.load_kw, s.solar_kw)
    ):
        raise ValueError("Non-finite daily forecast")
    if any(min(s.load_kw, s.solar_kw) < 0 for s in horizon):
        raise ValueError("Negative daily load/solar")
    return stamps


def plan_daily(
    slots,
    telemetry,
    settings,
    baseline_only=False,
    *,
    minimum_slots=96,
    current_schedule=None,
):
    if len(slots) < minimum_slots:
        raise ValueError(
            "Daily scheduling needs a complete published 24-hour horizon"
            if minimum_slots == 96
            else "Rolling scheduling needs at least one hour of published prices"
        )
    horizon = slots
    stamps = validate_standard_horizon(horizon)
    old = (
        legacy_periods(horizon, telemetry, settings)
        if len(horizon) >= 96
        else ((), None)
    )
    # Upload applies immediately, so avoid activating a charge period in the
    # still-in-progress quarter. The matched old candidate uses the same rule.
    blackout = (stamps[0] - timedelta(minutes=15)).astimezone(PRAGUE).strftime("%H:%M")
    old = (tuple(p for p in old[0] if not p[0] <= blackout < p[1]), old[1])
    candidates = [((), None), old]
    if current_schedule and not baseline_only:
        charge, discharge = (
            current_schedule.get("charge", {}),
            current_schedule.get("discharge", {}),
        )
        cp = (
            tuple(
                (charge[f"timeChaf{j}"], charge[f"timeChae{j}"])
                for j in (1, 2)
                if charge.get(f"timeChaf{j}") != charge.get(f"timeChae{j}")
            )
            if charge.get("gridCharge")
            else ()
        )
        dp = (
            tuple(
                (discharge[f"timeDisf{j}"], discharge[f"timeDise{j}"])
                for j in (1, 2)
                if discharge.get(f"timeDisf{j}") != discharge.get(f"timeDise{j}")
            )
            if discharge.get("ctrDis")
            else None
        )
        if not (dp is not None and any(a < y and x < b for a, b in cp for x, y in dp)):
            # An installed charging window may already be active. Include it
            # ahead of new candidates so equal-cost replans preserve continuity
            # instead of pausing charge until the next quarter hour.
            candidates.append((cp, dp))
    if not baseline_only:
        cw = _windows(horizon, (1, 2, 4, 6, 8, 12, 16, 24, 32), True, blackout=blackout)
        dw = _windows(horizon, (4, 8, 12, 16, 24, 32, 48, 64), False)
        co = _options(cw)
        do = [None] + [d for d in _options(dw) if d]
        co.append(old[0])
        do.append(old[1])
        # Explicit full-night options, including a night spanning midnight.
        co.extend(
            [
                (("00:00", "06:00"),),
                (("01:00", "07:00"),),
                (("02:00", "08:00"),),
                (("00:00", "06:00"), ("22:00", "23:45")),
            ]
        )
        do.extend([(("06:00", "23:45"),), (("05:00", "11:00"), ("16:00", "23:45"))])
        for c in co:
            if any(a <= blackout < b for a, b in c):
                continue
            for d in do:
                if d is not None and any(a < y and x < b for a, b in c for x, y in d):
                    continue
                candidates.append((c, d))
    candidates = list(dict.fromkeys(candidates))
    capacities = settings.capacity_scenarios_kwh or [telemetry.capacity_kwh]
    scenario_scores = np.array(
        [
            score_candidates(
                horizon,
                replace(telemetry, capacity_kwh=c),
                settings,
                candidates,
                enforce_headroom=not baseline_only,
            )
            for c in capacities
        ]
    )
    old_index = candidates.index(old)
    feasible = np.all(np.isfinite(scenario_scores), axis=0)
    if np.all(np.isfinite(scenario_scores[:, old_index])):
        feasible &= np.all(
            scenario_scores <= scenario_scores[:, old_index, None] + 1e-6, axis=0
        )
    scores = np.where(feasible, np.mean(scenario_scores, axis=0), np.inf)
    # baseline_only executes the original candidate, rather than silently
    # selecting native. Full search always retains native and old candidates.
    index = candidates.index(old) if baseline_only else int(np.argmin(scores))
    if not math.isfinite(scores[index]):
        raise ValueError("Candidate charging exceeds forecast import headroom")
    c, d = candidates[index]
    cp, dp = payloads(c, d, settings)
    _, decisions = simulate(
        horizon, telemetry, settings, c, settings.maximum_percent, True, d
    )
    old_score = float(scores[candidates.index(old)])
    return {
        "charge": cp,
        "discharge": dp,
        "version": 2,
        "capacity_scenarios_kwh": capacities,
        "valid_from": horizon[0].timestamp,
        "valid_until": min(
            stamps[0] + timedelta(hours=24), stamps[-1] + timedelta(minutes=15)
        ).isoformat(),
        "lookahead_until": (stamps[-1] + timedelta(minutes=15)).isoformat(),
        # Dominance filtering can exclude native even though its physical cost
        # is finite. Report economics from raw scenario costs, not the
        # feasibility sentinel used to select a schedule.
        "forecast_benefit_eur": round(
            float(np.mean(scenario_scores[:, 0]) - scores[index]), 4
        ),
        "forecast_improvement_vs_legacy_eur": None
        if not math.isfinite(old_score)
        else round(old_score - float(scores[index]), 6),
        "candidate_count": len(candidates),
        "selected": "legacy"
        if (c, d) == old
        else "native"
        if not c and d is None
        else "optimized",
        "notes": [
            "Two charge and two household-discharge windows; charging may reach full SOC when economical.",
            "Uses all published price lookahead, expected demand, solar, losses and assumed wear; no battery export.",
            "Forecast score includes the original strategy as a candidate; realized savings are not guaranteed.",
            "Replanned from current SOC using published prices; power follows the appliance setting and windows repeat until replaced.",
        ],
        "decisions": [x.dict() for x in decisions[:96]],
        "outlook_decisions": [x.dict() for x in decisions],
    }
