import asyncio
import datetime
import logging
from typing import List, Tuple, Dict

import httpx
import yaml
from alphaess.alphaess import alphaess

# Constants
MIDNIGHT = "00:00"
CONFIG_PATH = "config.yaml"
LOG_FILE = "ESS.log"
PRICE_URL = 'https://www.ote-cr.cz/en/short-term-markets/electricity/day-ahead-market/@@chart-data?report_date='

# Configure logging for cwd of file
logging.basicConfig(level=logging.INFO, filename=LOG_FILE, format='%(asctime)s %(levelname)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')


def load_config(path: str) -> Dict:
    with open(path) as f:
        return yaml.safe_load(f)


def validate_config(config: Dict) -> None:
    required_keys = ["username", "password", "price_multiplier", "charge_to_full", "serial_number"]
    for key in required_keys:
        if key not in config:
            raise ValueError(f"{key} not found in config")


def get_prices_from_json(prices_json: Dict) -> List[float]:
    return [float(point['y']) for point in prices_json['data']['dataLine'][1]['point']]


def find_cheapest_night_charging(electricity_prices: List[float], hours_to_charge: int) -> Tuple[int, float]:
    num_hours_considered = 7  # The number of hours considered for night-time charging
    if hours_to_charge > num_hours_considered:
        raise ValueError("hours_to_charge cannot exceed the number of hours considered for night-time.")

    min_mean = float('inf')
    electricity_prices = electricity_prices[:num_hours_considered]  # Considering only the first 7 hours of the day.
    index = 0

    for i in range(len(electricity_prices) - hours_to_charge + 1):
        mean = sum(electricity_prices[i:i + hours_to_charge]) / hours_to_charge
        if mean < min_mean:
            min_mean = mean
            index = i

    return index, min_mean


async def fetch_prices_for_date(date: datetime.date) -> List[float]:
    url_date = date.strftime('%Y-%m-%d')
    async with httpx.AsyncClient() as client:
        response = await client.get(PRICE_URL + url_date)
        response.raise_for_status()
        return get_prices_from_json(response.json())


class AlphaESSManager:
    def __init__(self, config: Dict):
        self.config = config
        self.client = alphaess()

    async def authenticate_and_set_schedule(self, should_charge: bool, start_charging: str, stop_charging: str) -> None:
        for i in range(3):
            if await self.authenticate_and_send_to_ess(should_charge, start_charging, stop_charging):
                logging.info("Successfully authenticated and sent request to ESS\n")
                break
        else:
            logging.error("Failed to authenticate or send request to ESS after attempts... giving up\n")

    async def authenticate_and_send_to_ess(self, should_charge: bool, start_charging: str, stop_charging: str) -> bool:
        username = self.config["username"]
        password = self.config["password"]
        serial_number = self.config.get("serial_number", "your_serial_number")

        try:
            await self.client.authenticate(username=username, password=password)

            # Set the charge and discharge hours
            await self.client.setbatterycharge(serial_number, should_charge, start_charging, stop_charging,
                                               MIDNIGHT, MIDNIGHT, 100)
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logging.error(
                    "Authentication with the provided details failed. Please check your username and password")
            else:
                logging.error(f"HTTP error occurred: {e}")
        except Exception as e:
            logging.error(f"An error occurred: {e}")
        return False


async def main():
    config = load_config(CONFIG_PATH)
    validate_config(config)

    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    prices = await fetch_prices_for_date(tomorrow)
    start_charge_hour, mean_charge_price = find_cheapest_night_charging(prices, config["charge_to_full"])
    stop_charge_hour = start_charge_hour + config["charge_to_full"]
    logging.info(f"Found cheapest charging hours to be {start_charge_hour}:00 - {stop_charge_hour}:00 "
                 f"with mean price of {mean_charge_price:.1f} €/MWh")

    manager = AlphaESSManager(config)

    # Format hours into strings
    start_charging = f"{start_charge_hour:02d}:00"
    stop_charging = f"{stop_charge_hour:02d}:00"

    await manager.authenticate_and_set_schedule(True, start_charging, stop_charging)


if __name__ == '__main__':
    asyncio.run(main())
