"""Translate rolling EMHASS modes to the standard two-window AlphaESS API."""

from datetime import datetime, timedelta

from .daily import in_window, payloads, score_candidates, validate_standard_horizon
from .domain import PRAGUE


def schedule_from_decisions(
    decisions, slots, telemetry, settings, current_schedule=None
):
    validate_standard_horizon(slots)
    if len(decisions) != len(slots) or any(
        d.timestamp != s.timestamp for d, s in zip(decisions, slots)
    ):
        raise ValueError("EMHASS decisions do not match the forecast horizon")
    first = datetime.fromisoformat(decisions[0].timestamp)
    command_day = first.astimezone(PRAGUE).date()
    periods = {"charge": [], "self_consume": []}
    for d in decisions:
        t = datetime.fromisoformat(d.timestamp)
        # Repeating HH:MM windows have no date. Tomorrow's mode must not
        # accidentally change today's in-progress quarter with the same clock.
        if t.astimezone(PRAGUE).date() != command_day:
            break
        end = (t + timedelta(minutes=15)).astimezone(PRAGUE)
        start = t.astimezone(PRAGUE)
        # Device accepts no end later than23:45. Boundary projection is
        # reported rather than silently promising an unrepresentable period.
        a = start.strftime("%H:%M")
        b = end.strftime("%H:%M")
        if end.date() != start.date():
            b = "23:45"
        if d.action not in periods or a >= b:
            continue
        out = periods[d.action]
        if out and out[-1][1] == a:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    # Uploads take effect immediately: retain the in-progress quarter's action
    # before a new future mode, where it has a representable wall-clock end.
    current = (first - timedelta(minutes=15)).astimezone(PRAGUE)
    end = first.astimezone(PRAGUE)
    if current_schedule and current.date() == end.date():
        c = current_schedule.get("charge", {})
        d = current_schedule.get("discharge", {})
        cp = (
            [
                (c.get(f"timeChaf{j}", "00:00"), c.get(f"timeChae{j}", "00:00"))
                for j in (1, 2)
            ]
            if c.get("gridCharge")
            else []
        )
        dp = (
            [
                (d.get(f"timeDisf{j}", "00:00"), d.get(f"timeDise{j}", "00:00"))
                for j in (1, 2)
            ]
            if d.get("ctrDis")
            else None
        )
        action = (
            "charge"
            if in_window(current, cp)
            else "self_consume"
            if dp is None or in_window(current, dp)
            else "hold"
        )
        if action in periods:
            a, b = current.strftime("%H:%M"), end.strftime("%H:%M")
            out = periods[action]
            if out and out[0][0] == b:
                out[0] = (a, out[0][1])
            else:
                out.insert(0, (a, b))
    c, d = tuple(periods["charge"][:2]), tuple(periods["self_consume"][:2])
    # Temporal order can cross midnight; sort periods for the device contract.
    c, d = tuple(sorted(c)), tuple(sorted(d))
    if not d:
        # ctrDis=1 with zero periods is rejected by the device. Use a window
        # outside the committed and in-progress quarters. Rolling updates
        # replace this placeholder before it can become active.
        blackout = {
            first.astimezone(PRAGUE).strftime("%H:%M"),
            current.strftime("%H:%M"),
        }
        clock = current.strftime("%H:%M")
        candidates = list(range(95))
        candidates.sort(key=lambda i: (f"{i // 4:02d}:{i % 4 * 15:02d}" >= clock, -i))
        for i in candidates:
            a = f"{i // 4:02d}:{i % 4 * 15:02d}"
            b = f"{(i + 1) // 4:02d}:{(i + 1) % 4 * 15:02d}"
            if a not in blackout and not any(x < b and a < y for x, y in c):
                d = ((a, b),)
                break
        else:
            # Charging fills every representable period; native discharge
            # outside it cannot change the committed charge action.
            d = None
    charge, discharge = payloads(c, d, settings)
    from .legacy_device import LegacyDevice

    LegacyDevice.validate_settings(charge, "charge")
    LegacyDevice.validate_settings(discharge, "discharge")
    if d is not None and any(a < y and x < b for a, b in c for x, y in d):
        raise ValueError("EMHASS charge/discharge windows overlap")
    first_charge = in_window(first, c)
    first_supply = d is None or in_window(first, d)
    first_action = (
        "charge" if first_charge else "self_consume" if first_supply else "hold"
    )
    raw = score_candidates(slots, telemetry, settings, [((), None), (c, d)])
    projected = first_action != decisions[0].action
    return {
        "charge": charge,
        "discharge": discharge,
        "version": 3,
        "capacity_scenarios_kwh": [telemetry.capacity_kwh],
        "valid_from": first.isoformat(),
        "valid_until": (first + timedelta(minutes=30)).isoformat(),
        "lookahead_until": (
            datetime.fromisoformat(slots[-1].timestamp) + timedelta(minutes=15)
        ).isoformat(),
        "forecast_benefit_eur": float(raw[0] - raw[1]),
        "forecast_improvement_vs_legacy_eur": None,
        "candidate_count": None,
        "selected": {"charge": c, "discharge": d},
        "decisions": [d.dict() for d in decisions[:96]],
        "outlook_decisions": [d.dict() for d in decisions],
        "planner": "emhass",
        "first_action_projection": projected,
        "committed_first_action": first_action,
        "notes": [
            "EMHASS rolling MPC; fixed-power charging, automatic PV capture, household-only discharge.",
            "Only the next rolling action is committed; up to two charge and two discharge windows are installed and refreshed every15minutes.",
            *(
                [
                    "Midnight boundary action projected to a representable standard-API mode."
                ]
                if projected
                else []
            ),
        ],
    }
