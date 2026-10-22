"""Fetch historical Czech day-ahead prices from the OTE API."""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ote_cr_price_fetcher import PriceFetcher


def dates_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


async def fetch_day(
    fetcher: PriceFetcher,
    query_date: date,
    semaphore: asyncio.Semaphore,
) -> tuple[date, list[float]]:
    async with semaphore:
        prices = await fetcher.fetch_prices_for_date(query_date)
    return query_date, prices


async def run(args: argparse.Namespace) -> None:
    fetcher = PriceFetcher(max_retry_time=30)
    semaphore = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(
        *(fetch_day(fetcher, day, semaphore) for day in dates_between(args.start, args.end))
    )

    rows = []
    prague = ZoneInfo("Europe/Prague")
    utc = ZoneInfo("UTC")
    for query_date, prices in sorted(results):
        local_start = datetime.combine(query_date, time.min, tzinfo=prague)
        local_end = datetime.combine(query_date + timedelta(days=1), time.min, tzinfo=prague)
        utc_start = local_start.astimezone(utc)
        interval_minutes = 15 if len(prices) in {92, 96, 100} else 60
        expected_points = int((local_end.astimezone(utc) - utc_start).total_seconds() / (interval_minutes * 60))
        if len(prices) == expected_points:
            for index, price in enumerate(prices):
                timestamp = (utc_start + timedelta(minutes=index * interval_minutes)).astimezone(prague)
                rows.append(
                    {
                        "timestamp": timestamp.isoformat(),
                        "price_eur_per_mwh": price,
                    }
                )
        else:
            print(
                f"Skipping incomplete OTE day {query_date}: "
                f"expected {expected_points} interval prices, got {len(prices)}"
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "price_eur_per_mwh"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} OTE price records to {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("analysis/output/ote_prices_2025.csv"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
