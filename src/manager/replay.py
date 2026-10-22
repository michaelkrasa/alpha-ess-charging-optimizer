"""Read-only historical evaluation; never opens a device connection."""

import csv
from datetime import datetime
import statistics

from .domain import PRAGUE, UTC, Slot, Telemetry, day_slots, utc_now
from .planner import optimize
from .tariff import prices, high_tariff, schedule_verified


async def replay(
    day,
    settings,
    store,
    providers,
    power_file="analysis/output/alphaess_power_2025.csv",
):
    samples = {}
    with open(power_file) as source:
        for row in csv.DictReader(source):
            if row["timestamp"][:10] != str(day):
                continue
            t = (
                datetime.fromisoformat(row["timestamp"])
                .replace(tzinfo=PRAGUE)
                .astimezone(UTC)
            )
            bucket = int(t.timestamp()) // 900
            samples.setdefault(bucket, []).append(
                (
                    float(row["load_kw"]),
                    float(row["pv_kw"]),
                    float(row["battery_soc_pct"]),
                )
            )
    times = day_slots(day)
    if any(int(t.timestamp()) // 900 not in samples for t in times):
        raise ValueError("Historical file does not cover the requested day")
    market = await providers.market(day)
    # Historical EUR conversion at the requested date, not today's rate.
    import httpx

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            "https://www.cnb.cz/en/financial_markets/foreign_exchange_market/exchange_rate_fixing/daily.txt",
            params={"date": day.strftime("%d.%m.%Y")},
        )
        response.raise_for_status()
    fx = next(
        float(row.split("|")[4])
        for row in response.text.splitlines()[2:]
        if row.split("|")[3] == "EUR"
    )
    slots = []
    observed = []
    for t, p in zip(times, market):
        rows = samples[int(t.timestamp()) // 900]
        load = statistics.mean(r[0] for r in rows)
        pv = statistics.mean(r[1] for r in rows)
        high = (
            True
            if not schedule_verified(t, settings)
            else high_tariff(t, settings.high_tariff_periods)
        )
        buy, sell = prices(p, fx, high)
        slots.append(
            Slot(t.isoformat(), p, buy, sell, load, pv, "VT" if high else "NT")
        )
        observed.append(
            {
                "timestamp": t.isoformat(),
                "load_kw": load,
                "solar_kw": pv,
                "soc_percent": statistics.mean(r[2] for r in rows),
            }
        )
    initial_soc = max(settings.reserve_percent, observed[0]["soc_percent"])
    battery = Telemetry(
        times[0].isoformat(),
        initial_soc,
        17.2,
        slots[0].load_kw,
        slots[0].solar_kw,
        0,
        0,
        "Historical 17.2 kWh assumption",
    )
    plan = optimize(slots, battery, settings)
    result = {
        "created_at": utc_now().isoformat(),
        "source": "historical_replay",
        "day": str(day),
        "notes": [
            "Historical replay with actual future load and solar: this is a hindsight benchmark, not a forecast or measured savings.",
            "Historical battery capacity assumed 17.2 kWh; initial charge raised to reserve if recorded below it.",
            "VT costs used for all intervals while HDO schedule is missing."
            if not all(schedule_verified(t, settings) for t in times)
            else "Configured HDO periods applied.",
        ],
        "decisions": [d.dict() for d in plan],
        "observed": observed,
    }
    store.put("replay", result)
    return result
