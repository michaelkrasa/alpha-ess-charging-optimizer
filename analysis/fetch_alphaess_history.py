"""Fetch AlphaESS daily energy history and derive site electrical load.

The exported CSV deliberately excludes the system serial number and API
credentials. Daily load is reconstructed from the AlphaESS energy balance:

    load = PV + grid import + battery discharge
           - grid export - battery charge
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Config
from src.ess_client import ESSClient


FIELDS = (
    "date",
    "pv_kwh",
    "grid_import_kwh",
    "grid_export_kwh",
    "battery_charge_kwh",
    "battery_discharge_kwh",
    "grid_battery_charge_kwh",
    "charging_pile_kwh",
    "derived_load_kwh",
)


def dates_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


async def fetch_one(
    client: ESSClient,
    query_date: date,
    semaphore: asyncio.Semaphore,
    retries: int = 3,
) -> dict | None:
    for attempt in range(retries):
        async with semaphore:
            payload = await client.get_one_day_energy(query_date)
        if isinstance(payload, dict) and payload.get("theDate"):
            pv = float(payload.get("epv") or 0)
            grid_import = float(payload.get("eInput") or 0)
            grid_export = float(payload.get("eOutput") or 0)
            battery_charge = float(payload.get("eCharge") or 0)
            battery_discharge = float(payload.get("eDischarge") or 0)
            derived_load = pv + grid_import + battery_discharge - grid_export - battery_charge
            return {
                "date": payload["theDate"],
                "pv_kwh": pv,
                "grid_import_kwh": grid_import,
                "grid_export_kwh": grid_export,
                "battery_charge_kwh": battery_charge,
                "battery_discharge_kwh": battery_discharge,
                "grid_battery_charge_kwh": float(payload.get("eGridCharge") or 0),
                "charging_pile_kwh": float(payload.get("eChargingPile") or 0),
                "derived_load_kwh": round(derived_load, 3),
            }
        if attempt + 1 < retries:
            await asyncio.sleep(0.5 * (attempt + 1))
    return None


async def run(args: argparse.Namespace) -> None:
    config = Config(args.config)
    client = ESSClient(
        config["app_id"],
        config["app_secret"],
        config["serial_number"],
        config.get("min_soc", 10),
        config.get("max_soc", 100),
    )
    try:
        semaphore = asyncio.Semaphore(args.concurrency)
        rows = await asyncio.gather(
            *(fetch_one(client, day, semaphore) for day in dates_between(args.start, args.end))
        )
    finally:
        await client.close()

    valid_rows = sorted((row for row in rows if row), key=lambda row: row["date"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(valid_rows)

    if valid_rows:
        print(
            f"Wrote {len(valid_rows)} days from {valid_rows[0]['date']} "
            f"through {valid_rows[-1]['date']} to {args.output}"
        )
    else:
        print("No valid daily records returned")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2021, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today() - timedelta(days=1))
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("analysis/output/alphaess_daily_history.csv"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
