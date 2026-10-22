"""Read-only, resumable household/price archive for walk-forward evaluation.

Raw household data lives under ignored data/backtests/, never in the Lambda ZIP.
Calls only the documented AlphaESS history read endpoint and public price data.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.manager.device import AlphaDevice
from src.manager.settings import load_settings
from ote_cr_price_fetcher import PriceFetcher


async def fetch(start, end, root):
    load_settings()
    logging.getLogger("alphaess").setLevel(logging.CRITICAL)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    root = Path(root)
    (root / "household").mkdir(parents=True, exist_ok=True)
    (root / "prices").mkdir(exist_ok=True)
    device = AlphaDevice()
    fetcher = PriceFetcher(max_retry_time=10, timeout=15, connect_timeout=15)
    try:
        day = start
        while day <= end:
            dest = root / "household" / f"{day}.json"
            if not dest.exists():
                try:
                    rows = await device.power_history(day)
                    if not isinstance(rows, list) or len(rows) < 200:
                        raise ValueError("Insufficient history")
                    allowed = {
                        "uploadTime",
                        "ppv",
                        "load",
                        "cbat",
                        "feedIn",
                        "gridCharge",
                    }
                    safe = [
                        {k: v for k, v in row.items() if k in allowed} for row in rows
                    ]
                    dest.write_text(
                        json.dumps(
                            {
                                "day": str(day),
                                "source": "AlphaESS getOneDayPowerBySn",
                                "samples": safe,
                            },
                            allow_nan=False,
                        )
                    )
                    print("Household", day, "samples", len(rows), flush=True)
                except Exception as exc:
                    print("Household", day, "FAILED", type(exc).__name__, flush=True)
            prices = root / "prices" / f"{day}.json"
            if not prices.exists():
                try:
                    values = await fetcher.fetch_prices_for_date(day, hourly=False)
                    if len(values) not in {23, 24, 25, 92, 96, 100}:
                        raise ValueError("Incomplete prices")
                    prices.write_text(json.dumps(values, allow_nan=False))
                except Exception as exc:
                    print("Prices", day, "FAILED", type(exc).__name__, flush=True)
            day += timedelta(days=1)
    finally:
        await device.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 9, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 19))
    parser.add_argument("--root", default="data/backtests")
    args = parser.parse_args()
    asyncio.run(fetch(args.start, args.end, args.root))
