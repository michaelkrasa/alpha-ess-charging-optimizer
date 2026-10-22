"""Fetch redacted AlphaESS interval power history for load-shape analysis."""

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
    "timestamp",
    "pv_kw",
    "load_kw",
    "battery_soc_pct",
    "grid_export_kw",
    "grid_import_kw",
    "charging_pile_kw",
)


def dates_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


async def fetch_day(
    client: ESSClient,
    query_date: date,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    async with semaphore:
        payload = await client.get_one_day_power(query_date)
    rows = []
    for point in payload or []:
        timestamp = point.get("uploadTime")
        if not timestamp:
            continue
        rows.append(
            {
                "timestamp": timestamp,
                "pv_kw": float(point.get("ppv") or 0) / 1000,
                "load_kw": float(point.get("load") or 0) / 1000,
                "battery_soc_pct": float(point.get("cbat") or 0),
                "grid_export_kw": float(point.get("feedIn") or 0) / 1000,
                "grid_import_kw": float(point.get("gridCharge") or 0) / 1000,
                "charging_pile_kw": float(point.get("pchargingPile") or 0) / 1000,
            }
        )
    return rows


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
        days = await asyncio.gather(
            *(fetch_day(client, day, semaphore) for day in dates_between(args.start, args.end))
        )
    finally:
        await client.close()

    rows = sorted((row for day in days for row in day), key=lambda row: row["timestamp"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} interval records to {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("analysis/output/alphaess_power_2025.csv"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
