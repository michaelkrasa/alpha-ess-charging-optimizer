"""Fixed-power prefix charging followed by hold, within a market quarter."""

from datetime import datetime, timedelta

from .daily import step

TERMINAL_STOPS = frozenset({"completed", "expired", "cancelled"})


def active_stops(intents, now):
    return [
        r
        for r in intents
        if r.get("status") not in TERMINAL_STOPS
        and datetime.fromisoformat(r["start"]) <= now < datetime.fromisoformat(r["end"])
    ]


def stop_due(intent, observed, now):
    if datetime.fromisoformat(intent["stop_at"]) <= now:
        return True
    stamp, ceiling = observed.get("timestamp"), intent.get("soc_ceiling")
    return bool(
        stamp
        and ceiling is not None
        and datetime.fromisoformat(intent["start"])
        <= datetime.fromisoformat(stamp)
        <= now
        and (now - datetime.fromisoformat(stamp)).total_seconds() <= 120
        and observed.get("soc_percent", -1) >= ceiling
    )


def prepare_stops(existing, proposed, charge, revision, now):
    """Merge immutable deadlines and mask stopped quarters before any new write."""
    intents = {
        r["start"]: r
        for r in existing
        if datetime.fromisoformat(r["end"]) > now - timedelta(hours=1)
    }
    for r in proposed:
        if r["start"] not in intents or (
            intents[r["start"]].get("revision") != revision
            and datetime.fromisoformat(r["start"]) > now
        ):
            intents[r["start"]] = {**r, "revision": revision, "status": "pending"}
    for r in intents.values():
        if (
            r.get("status") in ("stopping", "completed")
            or datetime.fromisoformat(r["stop_at"]) <= now
        ) and (
            datetime.fromisoformat(r["start"]) <= now < datetime.fromisoformat(r["end"])
        ):
            charge = mask_stopped_charge(charge, r)
    return list(intents.values()), charge


def partial_step(
    energy, load, solar, minutes, target, capacity, settings, discharge=False
):
    if minutes not in (0, 5, 10, 15):
        raise ValueError("Unsupported partial charge duration")
    if not minutes:
        return step(energy, load, solar, False, target, capacity, settings, discharge)
    charged = step(
        energy,
        load,
        solar,
        True,
        target,
        capacity,
        settings,
        False,
        duration_hours=minutes / 60,
    )
    if minutes == 15:
        return charged
    held = step(
        charged[0],
        load,
        solar,
        False,
        target,
        capacity,
        settings,
        False,
        duration_hours=(15 - minutes) / 60,
    )
    return (held[0], *(a + b for a, b in zip(charged[1:], held[1:])))


def stop_intents(decisions):
    return [
        {
            "start": d.timestamp,
            "stop_at": (
                datetime.fromisoformat(d.timestamp)
                + timedelta(minutes=d.charge_minutes)
            ).isoformat(),
            "end": (
                datetime.fromisoformat(d.timestamp) + timedelta(minutes=15)
            ).isoformat(),
            "soc_ceiling": d.soc_percent,
        }
        for d in decisions
        if d.action == "charge" and d.charge_minutes in (5, 10)
    ]


def mask_stopped_charge(charge, intent):
    """Remove one containing quarter and retain later charge periods."""
    from .domain import PRAGUE

    a = datetime.fromisoformat(intent["start"]).astimezone(PRAGUE).strftime("%H:%M")
    b = datetime.fromisoformat(intent["end"]).astimezone(PRAGUE).strftime("%H:%M")
    periods = []
    if charge["gridCharge"]:
        for j in (1, 2):
            x, y = charge[f"timeChaf{j}"], charge[f"timeChae{j}"]
            if x >= y:
                continue
            if x < a:
                periods.append((x, min(y, a)))
            if y > b:
                periods.append((max(x, b), y))
            if y <= a or x >= b:
                # The clauses above already retain disjoint periods.
                continue
    periods = sorted(set((x, y) for x, y in periods if x < y))
    # Past portions are unnecessary and must not displace future windows.
    periods = [(x, y) for x, y in periods if y > b][:2]
    out = {**charge, "gridCharge": int(bool(periods))}
    for j in (1, 2):
        out[f"timeChaf{j}"], out[f"timeChae{j}"] = (
            periods[j - 1] if len(periods) >= j else ("00:00", "00:00")
        )
    return out
