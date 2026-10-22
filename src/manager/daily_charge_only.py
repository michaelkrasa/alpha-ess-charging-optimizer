"""Two-window, charge-only daily planning for the standard AlphaESS API.

Native PV capture and household self-consumption are simulated explicitly. The
standard API cannot command charge watts or an arbitrary rolling hold action.
This bounded candidate search is a heuristic, not a globally optimal MILP.
"""

from datetime import datetime, timedelta
import math

from .domain import PRAGUE, Decision


def in_window(stamp, periods):
    clock = stamp.astimezone(PRAGUE).strftime("%H:%M")
    return any(start <= clock < end for start, end in periods)


def step(energy, load, solar, charge, target, capacity, settings, duration_hours=0.25):
    """One quarter-hour. Configured appliance charge power must be verified.

    Returns energy, import, export, discharge, grid charge, PV charge (kWh).
    No dynamic import clipping: the standard API has no power setpoint.
    """
    floor = capacity * settings.reserve_percent / 100
    ceiling = capacity * settings.maximum_percent / 100
    deficit, surplus = max(0, load - solar), max(0, solar - load)
    pv = max(
        0,
        min(
            surplus,
            settings.charge_kw,
            (ceiling - energy) / (duration_hours * settings.charge_efficiency),
        ),
    )
    energy += pv * duration_hours * settings.charge_efficiency
    grid = discharge = 0.0
    if charge:
        grid = max(
            0,
            min(
                settings.charge_kw - pv,
                (capacity * target / 100 - energy)
                / (duration_hours * settings.charge_efficiency),
            ),
        )
        energy += grid * duration_hours * settings.charge_efficiency
    else:
        discharge = max(
            0,
            min(
                deficit,
                settings.discharge_kw,
                (energy - floor) * settings.discharge_efficiency / duration_hours,
            ),
        )
        energy -= discharge * duration_hours / settings.discharge_efficiency
    return (
        energy,
        (deficit + grid - discharge) * duration_hours,
        (surplus - pv) * duration_hours,
        discharge * duration_hours,
        grid * duration_hours,
        pv * duration_hours,
    )


def simulate(slots, telemetry, settings, periods, target, details=False):
    energy = telemetry.capacity_kwh * telemetry.soc_percent / 100
    cash = wear = 0.0
    decisions = []
    for slot in slots:
        charge = in_window(datetime.fromisoformat(slot.timestamp), periods)
        energy, imported, exported, discharged, grid, pv = step(
            energy,
            slot.load_kw,
            slot.solar_kw,
            charge,
            target,
            telemetry.capacity_kwh,
            settings,
        )
        # Reject charging that would add a forecast overload. This cannot enforce
        # headroom against unexpected demand without an appliance-side limiter.
        if (
            settings.import_limit_kw is not None
            and grid > 1e-8
            and imported / 0.25 > settings.import_limit_kw + 1e-7
        ):
            return math.inf, []
        cash += imported * slot.import_eur_kwh - exported * slot.export_eur_kwh
        wear += discharged * settings.wear_eur_per_kwh
        if details:
            action = "charge" if charge else "self_consume"
            decisions.append(
                Decision(
                    slot.timestamp,
                    action,
                    grid / 0.25,
                    discharged / 0.25,
                    pv / 0.25,
                    energy / telemetry.capacity_kwh * 100,
                    slot.import_eur_kwh,
                    slot.export_eur_kwh,
                    slot.load_kw,
                    slot.solar_kw,
                    "Daily grid-charge window; power follows the appliance setting."
                    if charge
                    else "Native solar capture and household self-consumption above reserve.",
                )
            )
    terminal = max(
        0,
        min(s.import_eur_kwh for s in slots) * settings.discharge_efficiency
        - settings.wear_eur_per_kwh,
    )
    return cash + wear - energy * terminal, decisions


def plan_daily(slots, telemetry, settings):
    if len(slots) < 96:
        raise ValueError("Daily scheduling needs a complete published 24-hour horizon")
    horizon = slots[:96]
    stamps = [datetime.fromisoformat(s.timestamp) for s in horizon]
    if any(t.tzinfo is None for t in stamps) or any(
        (b - a).total_seconds() != 900 for a, b in zip(stamps, stamps[1:])
    ):
        raise ValueError(
            "Daily horizon must be contiguous timezone-aware quarter hours"
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
    # For each duration retain three cheapest forecast windows. Windows cannot
    # cross midnight; 23:45 is the documented maximum endpoint.
    candidates = set()
    for length in (1, 2, 4, 6, 8, 12, 16):
        ranked = []
        for i in range(len(horizon) - length + 1):
            start = stamps[i].astimezone(PRAGUE)
            end = (stamps[i + length - 1] + timedelta(minutes=15)).astimezone(PRAGUE)
            if start.date() != end.date():
                continue
            period = (start.strftime("%H:%M"), end.strftime("%H:%M"))
            # The daily API applies immediately. Do not accidentally activate a
            # next-day window in the quarter still in progress during upload.
            current_clock = (
                (stamps[0] - timedelta(minutes=15)).astimezone(PRAGUE).strftime("%H:%M")
            )
            if period[0] <= current_clock < period[1]:
                continue
            mean = sum(s.import_eur_kwh for s in horizon[i : i + length]) / length
            ranked.append((mean, period))
        candidates.update(p for _, p in sorted(ranked)[:3])
    options = [()] + [(p,) for p in sorted(candidates)]
    ordered = sorted(candidates)
    options += [
        (a, b) for i, a in enumerate(ordered) for b in ordered[i + 1 :] if a[1] <= b[0]
    ]
    # Do not assume batHighCap limits grid charging alone: a lower shared
    # charge cutoff could also curtail PV on some firmware. Vary durations.
    targets = [int(settings.maximum_percent)]
    best_periods = ()
    best_target = int(settings.maximum_percent)
    best, _ = simulate(horizon, telemetry, settings, (), best_target)
    baseline = best
    for periods in options[1:]:
        for target in targets:
            score, _ = simulate(horizon, telemetry, settings, periods, target)
            if score < best - 1e-6:
                best, best_periods, best_target = score, periods, target
    _, decisions = simulate(
        horizon, telemetry, settings, best_periods, best_target, details=True
    )
    padded = list(best_periods) + [("00:00", "00:00")] * (2 - len(best_periods))
    schedule = {
        "gridCharge": int(bool(best_periods)),
        "batHighCap": best_target,
        "timeChaf1": padded[0][0],
        "timeChae1": padded[0][1],
        "timeChaf2": padded[1][0],
        "timeChae2": padded[1][1],
    }
    return {
        "charge": schedule,
        "valid_from": horizon[0].timestamp,
        "valid_until": (stamps[-1] + timedelta(minutes=15)).isoformat(),
        "forecast_benefit_eur": round(baseline - best, 4),
        "notes": [
            "Two daily charge windows, one SOC target, native household discharge; no power setpoint or hold.",
            "Appliance configured charge power is assumed to match charge_kw; unexpected load needs appliance-side protection.",
            "Schedule repeats daily until changed; forecast expiry does not cancel the inverter schedule.",
        ],
        "decisions": [d.dict() for d in decisions],
    }
