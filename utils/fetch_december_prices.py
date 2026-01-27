#!/usr/bin/env python3
"""
Fetch December 2025 price data for comprehensive testing.

Downloads 15-minute price data for all days in December 2025 and saves
each day as a separate JSON file in test_data/december_2025/.
"""

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

from ote_cr_price_fetcher import PriceFetcher

from src.models import SLOTS_PER_DAY

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Directory for storing price data
DATA_DIR = Path('test_data/december_2025')
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Number of concurrent workers
MAX_WORKERS = 3


async def fetch_day_prices(fetcher: PriceFetcher, target_date: date, semaphore: asyncio.Semaphore) -> tuple[date, list[float] | None, Exception | None]:
    """Fetch prices for a single day with semaphore-controlled concurrency."""
    async with semaphore:
        date_str = str(target_date)
        logger.info(f"Fetching prices for {date_str}...")
        
        try:
            prices_list = await fetcher.fetch_prices_for_date(target_date, hourly=False)
            
            if not prices_list:
                logger.warning(f"No price data returned for {date_str}")
                return target_date, None, None
            
            if len(prices_list) != SLOTS_PER_DAY:
                error_msg = f"Invalid price data for {date_str}: expected {SLOTS_PER_DAY} slots, got {len(prices_list)}"
                logger.error(error_msg)
                return target_date, None, ValueError(error_msg)
            
            logger.info(f"✓ Successfully fetched {len(prices_list)} prices for {date_str}")
            return target_date, prices_list, None
            
        except Exception as e:
            logger.error(f"✗ Failed to fetch prices for {date_str}: {e}")
            return target_date, None, e


async def save_day_prices(target_date: date, prices: list[float]) -> None:
    """Save price data to JSON file."""
    date_str = str(target_date)
    file_path = DATA_DIR / f"{date_str}.json"
    
    try:
        with open(file_path, 'w') as f:
            json.dump(prices, f, indent=2)
        logger.info(f"Saved {date_str} to {file_path}")
    except Exception as e:
        logger.error(f"Failed to save {date_str}: {e}")
        raise


async def fetch_all_december_prices():
    """Fetch prices for all days in December 2025."""
    fetcher = PriceFetcher()
    semaphore = asyncio.Semaphore(MAX_WORKERS)
    
    # Generate all dates in December 2025
    dates = [date(2025, 12, day) for day in range(1, 32)]
    
    logger.info(f"Starting to fetch prices for {len(dates)} days in December 2025")
    logger.info(f"Using {MAX_WORKERS} concurrent workers")
    
    # Fetch all dates concurrently (limited by semaphore)
    tasks = [fetch_day_prices(fetcher, d, semaphore) for d in dates]
    results = await asyncio.gather(*tasks)
    
    # Process results
    successful = 0
    failed = 0
    missing = 0
    
    for target_date, prices, error in results:
        if error is not None:
            failed += 1
            continue
        
        if prices is None:
            missing += 1
            continue
        
        # Save successful fetches
        try:
            await save_day_prices(target_date, prices)
            successful += 1
        except Exception as e:
            logger.error(f"Failed to save {target_date}: {e}")
            failed += 1
    
    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"Fetch Summary:")
    logger.info(f"  Successful: {successful}/{len(dates)}")
    logger.info(f"  Missing data: {missing}/{len(dates)}")
    logger.info(f"  Failed: {failed}/{len(dates)}")
    logger.info(f"{'='*60}")


async def main():
    """Main entry point."""
    try:
        await fetch_all_december_prices()
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
