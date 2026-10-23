"""Causal forecast-risk evidence and measured full-charge maintenance state.

No device access. Retained forecasts are the first available forecasts for each
target quarter, so rolling revisions cannot erase an earlier forecast error.
"""

from datetime import datetime, timedelta
import math

import numpy as np

from .consumption import separate_grid_only_load
from .domain import PRAGUE, UTC


def archive_forecasts(store, slots, now):
    cutoff = now - timedelta(days=65)
    rows = {
        k: v
        for k, v in store.get("risk_forecasts", {}).items()
        if datetime.fromisoformat(k) >= cutoff
    }
    for slot in slots:
        t = datetime.fromisoformat(slot.timestamp)
        if t > now:
            rows.setdefault(
                t.astimezone(UTC).isoformat(),
                {
                    "issued_at": now.isoformat(),
                    "load_kw": slot.load_kw,
                    "solar_kw": slot.solar_kw,
                },
            )
    store.put("risk_forecasts", rows)


def record_errors(store, samples, now, separate_ev):
    forecasts = store.get("risk_forecasts", {})
    errors = {
        r["timestamp"]: r
        for r in store.get("risk_errors", [])
        if datetime.fromisoformat(r["timestamp"]) >= now - timedelta(days=60)
    }
    # Filter future samples before session inference; completed sessions may only
    # inform decisions after they have actually been observed.
    points = []
    for r in samples or []:
        t = datetime.fromisoformat(str(r.get("uploadTime") or r["time"]))
        if t.tzinfo is None:
            t = t.replace(tzinfo=PRAGUE)
        if t <= now:
            points.append((t.astimezone(UTC), r))
    points.sort(key=lambda x: x[0])
    cleaned = (
        separate_grid_only_load([r for _, r in points])
        if separate_ev
        else [r for _, r in points]
    )
    # The separator sorts and can discard invalid points; re-key by timestamp.
    cleaned_map = {str(r.get("uploadTime") or r.get("time")): r for r in cleaned}
    buckets = {}
    for (a, raw), (b, _) in zip(points, points[1:]):
        r = cleaned_map.get(str(raw.get("uploadTime") or raw.get("time")))
        if r is None or not 0 < (b - a).total_seconds() <= 600:
            continue
        load, pv = float(r["load"]) / 1000, float(r["ppv"]) / 1000
        if not all(math.isfinite(x) and x >= 0 for x in (load, pv)):
            continue
        while a < b:
            q = a.replace(minute=a.minute // 15 * 15, second=0, microsecond=0)
            end = min(b, q + timedelta(minutes=15))
            h = (end - a).total_seconds() / 3600
            v = buckets.setdefault(q.isoformat(), [0.0, 0.0, 0.0])
            v[0] += h
            v[1] += load * h
            v[2] += pv * h
            a = end
    for stamp, (hours, load, pv) in buckets.items():
        forecast = forecasts.get(stamp)
        if not forecast or abs(hours - 0.25) > 1e-7:
            continue
        t = datetime.fromisoformat(stamp)
        # Long-lead planning errors, not forecasts refreshed just before delivery.
        if t - datetime.fromisoformat(forecast["issued_at"]) < timedelta(hours=2):
            continue
        errors[stamp] = {
            "timestamp": stamp,
            "error_kw": load * 4 - pv * 4 - forecast["load_kw"] + forecast["solar_kw"],
        }
    store.put("risk_errors", sorted(errors.values(), key=lambda r: r["timestamp"]))


def buffer_request(slots, settings, errors=()):
    """One upcoming expensive exposure, with a bounded cumulative error hedge."""
    if not settings.hedge_enabled:
        return None
    # A worthwhile peak must cover loss and wear before adding any insurance.
    cheap = slots[0].import_eur_kwh
    peak = next(
        (
            i
            for i, r in enumerate(slots[1:], 1)
            if r.import_eur_kwh
            > cheap / (settings.charge_efficiency * settings.discharge_efficiency)
            + settings.wear_eur_per_kwh
            + 0.01
        ),
        None,
    )
    if peak is None:
        return None
    end = next(
        (
            i
            for i in range(peak + 1, len(slots))
            if slots[i].import_eur_kwh <= cheap + 0.01
        ),
        len(slots),
    )
    exposure = slots[peak:end]
    origin = datetime.fromisoformat(slots[0].timestamp)
    days = {}
    for row in errors:
        t = datetime.fromisoformat(row["timestamp"]).astimezone(PRAGUE)
        if t.date() >= origin.astimezone(PRAGUE).date():
            continue
        days.setdefault(t.date(), {})[t.hour * 4 + t.minute // 15] = row["error_kw"]
    clocks = [datetime.fromisoformat(r.timestamp).astimezone(PRAGUE) for r in exposure]
    # Do not reuse one day's errors twice across a horizon crossing midnight.
    offsets = [(t.date() - clocks[0].date()).days for t in clocks]
    shortfalls = []
    for day in days:
        total = worst = 0.0
        for t, offset in zip(clocks, offsets):
            value = days.get(day + timedelta(days=offset), {}).get(
                t.hour * 4 + t.minute // 15
            )
            if value is None:
                break
            total += value * 0.25 / settings.discharge_efficiency
            worst = max(worst, total)
        else:
            shortfalls.append(worst)
    if len(shortfalls) >= settings.hedge_min_days:
        extra = float(np.quantile(shortfalls, settings.hedge_quantile))
        source = "empirical cumulative forecast errors"
    else:
        # Explicit cold-start stress, not an asserted percentile/coverage level.
        extra = (
            sum((0.20 * r.load_kw + 0.30 * r.solar_kw) * 0.25 for r in exposure)
            / settings.discharge_efficiency
        )
        source = "provisional +20% load / -30% PV stress"
    return {
        "anchor": peak - 1,
        "buffer_kwh": min(settings.hedge_max_kwh, max(0.0, extra)),
        "premium_eur": settings.hedge_premium_eur,
        "source": source,
        "sample_days": len(shortfalls),
        "exposure_until": exposure[-1].timestamp,
    }


def observe_full_charge(store, telemetry, now):
    state = store.get("full_charge", {})
    state.setdefault("tracking_since", now.isoformat())
    if telemetry.soc_percent >= 99.9:
        state["last_full_soc_at"] = telemetry.timestamp
        # Full SOC is distinct from the vendor's full-charge/taper calibration.
        if abs(telemetry.battery_kw) <= 0.2:
            prior = state.get("taper_sample_at")
            if prior and timedelta(minutes=5) <= datetime.fromisoformat(
                telemetry.timestamp
            ) - datetime.fromisoformat(prior) <= timedelta(minutes=15):
                state["last_calibration_at"] = telemetry.timestamp
            state["taper_sample_at"] = telemetry.timestamp
    else:
        state.pop("taper_sample_at", None)
    store.put("full_charge", state)


def maintenance_due(settings, state, now):
    if not settings.full_charge_interval_days:
        return False
    last = state.get("last_calibration_at")
    return not last or now - datetime.fromisoformat(last) >= timedelta(
        days=settings.full_charge_interval_days
    )


def full_charge_deadline(settings, state, now):
    """Persist one due date; a rolling replan must never renew its deadline."""
    if not maintenance_due(settings, state, now):
        state.pop("due_since", None)
        return None
    state.setdefault("due_since", now.isoformat())
    return datetime.fromisoformat(state["due_since"]) + timedelta(days=1)


def maintenance_target(slots, allowed, needed, settings, deadline=None):
    """Cheapest feasible on-time fill, or earliest feasible fill if already late."""
    options = []
    for end in range(len(slots) - 1):
        if not all(allowed[end : end + 2]):
            continue
        remaining, cost = needed, 0.0
        for price in sorted(
            slots[j].import_eur_kwh for j in range(end + 1) if allowed[j]
        ):
            amount = min(
                remaining, settings.charge_kw * 0.25 * settings.charge_efficiency
            )
            remaining -= amount
            cost += amount / settings.charge_efficiency * price
        if remaining <= 1e-7:
            options.append((cost, end))
    if not options:
        return None
    on_time = [
        option
        for option in options
        if deadline is None
        or datetime.fromisoformat(slots[option[1]].timestamp) + timedelta(minutes=15)
        <= deadline
    ]
    return min(on_time)[1] if on_time else min(end for _, end in options)


def remaining_premium(bank, now, limit):
    day = str(now.astimezone(PRAGUE).date())
    return max(
        0.0, limit - (bank.get("reserved_eur", 0.0) if bank.get("day") == day else 0.0)
    )


def planner_options(settings, store, slots, now):
    """Load policy evidence and persistent deadlines without involving the writer."""
    if not (settings.hedge_enabled or settings.full_charge_interval_days):
        return {}
    state = store.get("full_charge", {})
    deadline = full_charge_deadline(settings, state, now)
    store.put("full_charge", state)
    return {
        "errors": store.get("risk_errors", []),
        "full_charge_due": deadline is not None,
        "maintenance_deadline": deadline,
        "premium_budget_eur": remaining_premium(
            store.get("hedge_budget", {}),
            datetime.fromisoformat(slots[0].timestamp),
            settings.hedge_premium_eur,
        ),
    }


def premium_reservation(bank, risk, quarter, now, limit):
    """Book any committed insurance deviation, including holding instead of using energy."""
    if not (risk.get("first_action_changed") or risk.get("first_extra_minutes", 0)):
        return bank
    day = str(now.astimezone(PRAGUE).date())
    if bank.get("day") != day:
        bank = {"day": day, "reserved_eur": 0.0, "quarters": []}
    amounts = bank.get("quarter_premiums", {})
    premium = max(0.0, risk.get("forecast_premium_eur", 0.0))
    prior = amounts.get(quarter, 0.0)
    # Never refund an ambiguous write. A revised legacy quarter has no known
    # individual amount: conservatively book its new premium in addition to the
    # retained legacy total instead of inventing a historical allocation.
    return {
        **bank,
        "reserved_eur": min(limit, bank["reserved_eur"] + max(0.0, premium - prior)),
        "quarters": list(dict.fromkeys([*bank["quarters"], quarter])),
        "quarter_premiums": {**amounts, quarter: max(prior, premium)},
    }


def reserve_premium(store, risk, quarter, now, limit):
    """Reserve forecast insurance cost once before a potentially ambiguous write."""
    bank = premium_reservation(store.get("hedge_budget", {}), risk, quarter, now, limit)
    if bank:
        store.put("hedge_budget", bank)
