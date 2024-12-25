import asyncio
import datetime
import logging
import os
from typing import List, Tuple

from alpha_ess_manager import AlphaESSManager
from config import Config
from price_fetcher import PriceFetcher
from datetime import time

# Constants
DIR_NAME = os.path.dirname(__file__)
MIDNIGHT = "00:00"
CONFIG_PATH = os.path.join(DIR_NAME, "config.yaml")
LOG_FILE = os.path.join(DIR_NAME, "ess.log")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    filename=LOG_FILE,
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class EnergyStorageOptimizer:
    def __init__(self):
        self.config = Config(CONFIG_PATH)
        self.price_fetcher = PriceFetcher()
        self.manager = AlphaESSManager(self.config)

    def find_optimal_charging_windows(self, prices: List[float], hours_to_charge: int) -> Tuple[List[Tuple[int, int]], float]:
        """Find the optimal charging window(s) during night hours (first 7 hours of the day)
        Returns: (windows, mean_price) where windows is a list of (start, end) tuples
        Example: ([(2,3), (6,8)], 25.5) means charge from 2-3 and 6-8 with average price of 25.5"""
        NIGHT_HOURS = 7
        if hours_to_charge > NIGHT_HOURS:
            raise ValueError(f"Charging duration ({hours_to_charge}h) cannot exceed night hours ({NIGHT_HOURS}h)")

        night_prices = prices[:NIGHT_HOURS]
        min_mean = float('inf')
        best_windows = []

        # First, try a single continuous window of the required length
        for start in range(NIGHT_HOURS - hours_to_charge + 1):
            end = start + hours_to_charge
            window = night_prices[start:end]
            mean_price = sum(window) / hours_to_charge
            if mean_price < min_mean:
                min_mean = mean_price
                best_windows = [(start, end)]

        # If a single window does not provide the best mean price, try all possible combinations of two windows that sum to hours_to_charge
        for window1_size in range(1, hours_to_charge):
            window2_size = hours_to_charge - window1_size
            
            # Try all possible positions for first window
            for start1 in range(NIGHT_HOURS - window1_size + 1):
                end1 = start1 + window1_size
                window1 = night_prices[start1:end1]
                
                # Try all possible positions for second window that don't overlap
                for start2 in range(end1, NIGHT_HOURS - window2_size + 1):
                    end2 = start2 + window2_size
                    window2 = night_prices[start2:end2]
                    
                    # Calculate mean price for both windows combined
                    total_price = sum(window1) + sum(window2)
                    mean_price = total_price / hours_to_charge
                    
                    if mean_price < min_mean:
                        min_mean = mean_price
                        best_windows = [(start1, end1), (start2, end2)]

        windows_str = ", ".join([f"{start},{end}" for start, end in best_windows])
        logging.info(f"Found optimal charging window(s): {windows_str} with mean price: {min_mean:.1f} €/MWh")
        return best_windows, min_mean

    def is_charging_profitable(self, mean_charge_price: float, daily_prices: List[float]) -> bool:
        """Determine if charging is profitable based on price multiplier and daily average"""
        daily_average = sum(daily_prices) / len(daily_prices)
        return mean_charge_price * self.config["price_multiplier"] <= daily_average

    async def optimize_charging_schedule(self):
        """Main optimization logic to determine and set charging schedule"""
        # Fetch tomorrow's prices
        tomorrow = datetime.date.today() + datetime.timedelta(days=0)
        prices = await self.price_fetcher.fetch_prices_for_date(tomorrow)

        # Find optimal charging windows
        windows, mean_price = self.find_optimal_charging_windows(
            prices, 
            self.config["charge_to_full"]
        )

        # Determine if charging is worth it and set schedule
        if not self.is_charging_profitable(mean_price, prices):
            logging.info("Charging is not profitable - disabling charging schedule")
            await self.manager.set_charging_schedule(False, MIDNIGHT, MIDNIGHT, MIDNIGHT, MIDNIGHT)
        else:
            # Convert first window to times
            start_time1 = f"{windows[0][0]:02d}:00"
            end_time1 = f"{windows[0][1]:02d}:00"
            
            # Convert second window to times (if it exists)
            start_time2 = MIDNIGHT
            end_time2 = MIDNIGHT
            if len(windows) > 1:
                start_time2 = f"{windows[1][0]:02d}:00"
                end_time2 = f"{windows[1][1]:02d}:00"
                
            logging.error(start_time1)
            logging.error(end_time1)
            
            
            await self.manager.set_charging_schedule(True, start_time1, end_time1, start_time2, end_time2)

async def main():
    optimizer = EnergyStorageOptimizer()
    await optimizer.optimize_charging_schedule()

if __name__ == '__main__':
    asyncio.run(main())
