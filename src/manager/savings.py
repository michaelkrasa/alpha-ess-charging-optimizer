"""Measured savings against the same house and solar without a battery.

Per elapsed quarter hour, the actual grid energy is priced against the
counterfactual grid energy (demand minus solar, surplus exported) at the billed
import/export prices. Battery losses and solar stored instead of exported are
therefore counted. Quarters lacking a grid measurement or a billed price are
skipped, never estimated. Fixed standing charges are excluded on both sides.
"""

import math
from datetime import date, datetime, timedelta

from .domain import PRAGUE, day_slots
from .tariff import high_tariff, prices, schedule_verified

QUARTER = timedelta(minutes=15)


def billed_prices(store, settings, day, now):
    """One cached market day as billed quarter-hour prices; [] when incomplete.

    Performs no market/API requests. Missing FX leaves billed prices unknown.
    """
    fx = store.get("eur_conversion") or {}
    rate = fx.get("rate")
    try:
        valid_fx = (
            rate is not None
            and math.isfinite(rate)
            and 10 < rate < 100
            and 0 <= (now.astimezone(PRAGUE).date() - date.fromisoformat(fx["published"])).days <= 7
        )
    except (KeyError, ValueError, TypeError):
        valid_fx = False
    values = store.get(f"prices:{settings.price_source}:{day}")
    instants = day_slots(day)
    if values is None or len(values) != len(instants):
        return []
    if any(not isinstance(p, (int, float)) or not math.isfinite(p) for p in values):
        return []
    result = []
    for t, spot in zip(instants, values):
        verified = schedule_verified(t, settings)
        high = high_tariff(t, settings.high_tariff_periods) if verified else True
        buy, sell = prices(spot, rate, high) if valid_fx else (None, None)
        result.append(
            {
                "timestamp": t.isoformat(),
                "spot_eur_mwh": spot,
                "import_eur_kwh": buy,
                "export_eur_kwh": sell,
                "tariff": "VT" if high else "NT",
                "tariff_verified": verified,
                "elapsed": t + QUARTER <= now,
            }
        )
    return result


def _cost(net_kw, buy, sell):
    return net_kw * (buy if net_kw > 0 else sell) / 4


def day_savings(rows, price_rows, now):
    """Savings in EUR over elapsed quarters with both a measurement and a price."""
    buckets = {}
    for row in rows:
        try:
            t = datetime.fromisoformat(row["timestamp"])
            values = (float(row["load_kw"]), float(row["solar_kw"]), float(row["grid_kw"]))
        except (KeyError, ValueError, TypeError):
            continue
        if t.tzinfo is None or not all(math.isfinite(v) for v in values):
            continue
        buckets.setdefault(t, []).append(values)
    saved = actual = without = 0.0
    quarters = 0
    for p in price_rows:
        start = datetime.fromisoformat(p["timestamp"])
        buy, sell = p.get("import_eur_kwh"), p.get("export_eur_kwh")
        if start + QUARTER > now or buy is None or sell is None:
            continue
        samples = [v for t, group in buckets.items() if start <= t < start + QUARTER for v in group]
        if not samples:
            continue
        load = sum(v[0] for v in samples) / len(samples)
        solar = sum(v[1] for v in samples) / len(samples)
        grid = sum(v[2] for v in samples) / len(samples)
        a, w = _cost(grid, buy, sell), _cost(load - solar, buy, sell)
        actual += a
        without += w
        saved += w - a
        quarters += 1
    return {
        "saved_eur": round(saved, 4),
        "cost_eur": round(actual, 4),
        "cost_without_battery_eur": round(without, 4),
        "quarters": quarters,
    }


def record_day(store, settings, day, rows, now, final=False):
    """Store one local day's savings; a final day is never recomputed."""
    ledger = store.get("savings") or {"days": {}}
    key = str(day)
    if ledger["days"].get(key, {}).get("final"):
        return ledger
    result = day_savings(rows, billed_prices(store, settings, day, now), now)
    if not result["quarters"] and key not in ledger["days"]:
        return ledger
    ledger["days"][key] = {**result, "final": final, "updated_at": now.isoformat()}
    ledger["since"] = min(ledger["days"])
    store.put("savings", ledger)
    return ledger


def needs_final(store, now):
    """Yesterday, if the ledger is missing its complete total."""
    yesterday = now.astimezone(PRAGUE).date() - timedelta(days=1)
    entry = (store.get("savings") or {"days": {}})["days"].get(str(yesterday))
    return None if entry and entry.get("final") else yesterday


def dashboard_savings(store, now):
    ledger = store.get("savings") or {}
    days = ledger.get("days") or {}
    if not days:
        return None
    today = now.astimezone(PRAGUE).date()
    pick = lambda d: days.get(str(d))
    return {
        "since": ledger.get("since"),
        "total_eur": round(sum(d["saved_eur"] for d in days.values()), 2),
        "days_counted": len(days),
        "today": pick(today),
        "yesterday": pick(today - timedelta(days=1)),
    }
