import asyncio
import datetime
import logging
import os

import aiohttp
import requests
import yaml
from alphaess.alphaess import alphaess
from requests.adapters import HTTPAdapter, Retry

"""
Check the electricity prices for the next day (usually available after 3PM) on the OTE-CR website and set the night charge and morning discharge hours
to maximize utility. 
"""

global config
midnight = "00:00"


def load_config():
    with open(os.path.join(os.path.dirname(__file__), "config.yaml")) as f:
        return yaml.load(f, Loader=yaml.FullLoader)


# Validate the config file for required fields
def validate_config():
    if "username" not in config:
        raise ValueError("Username not found in config")
    if "password" not in config:
        raise ValueError("Password not found in config")
    if "price_multiplier" not in config:
        raise ValueError("Price multiplier not found in config")
    if "charge_to_full" not in config:
        raise ValueError("Charge to full not found in config")


# Extract the prices from the json
def get_prices_from_json(prices_json):
    prices = []
    for point in prices_json['data']['dataLine'][1]['point']:
        prices.append(float(point['y']))

    return prices


# Find time range to minimize charging cost. Takes number of hours to charge as input.
# Only looks for the first 7 hours of the day, since the price is usually the lowest then.
def find_cheapest_night_charging(numbers: list, hours_to_charge: int):
    min_mean = max(numbers)
    numbers = numbers[0:7]  # 00:00 - 07:00

    for i in range(len(numbers) - hours_to_charge + 1):
        mean = sum(numbers[i:i + hours_to_charge]) / hours_to_charge
        if mean < min_mean:
            min_mean = mean
            index = i

    return index, min_mean


# Find time range to maximize price for discharging. Takes number of hours to discharge as input.
# Aims to start discharging before the price peak. Only looks for morning values since we want to discharge the battery during the day.
def find_best_morning_discharging(numbers: list, hours_to_discharge: int):
    max_mean = min(numbers)
    numbers = numbers[0:12]  # 00:00 - 12:00
    for i in range(len(numbers) - hours_to_discharge + 1):
        mean = sum(numbers[i:i + hours_to_discharge]) / hours_to_discharge
        if mean > max_mean:
            max_mean = mean
            index = i

    return index, max_mean


async def set_charge_and_discharge_hours(should_charge: bool, start_charging: str, stop_charging: str, start_discharging: str):
    for i in range(5):
        success = await authenticate_and_send_to_ess(should_charge, start_charging, stop_charging, start_discharging)
        if success:
            break
        logging.warning("Authentication and or sending to ESS failed, retrying in 30 seconds")
        await asyncio.sleep(10)
    if success:
        logging.info("Request successfully sent to ESS\n")
    else:
        logging.error("Failed to authenticate or send request to ESS after 5 attempts... giving up\n")


async def authenticate_and_send_to_ess(should_charge: bool, start_charging: str, stop_charging: str, start_discharging: str) -> bool:
    username = config["username"]
    password = config["password"]
    serial_number = config["serial_number"]

    client: alphaess = alphaess()
    try:
        await client.authenticate(username=username, password=password)

        if serial_number == "your_serial_number":  # unset in config
            data = await client.getdata()
            if not data or "sys_sn" not in data[0]:
                logging.error("Could not get serial number from data")
                return False
            serial_number = data[0]['sys_sn']
            logging.info("Your serial number is %s, consider adding it to the config", serial_number, extra={"color": "yellow"})

        # Set the charge and discharge hours
        await client.setbatterycharge(serial_number, should_charge, start_charging, stop_charging, midnight, midnight, 100)
        await client.setbatterydischarge(serial_number, should_charge, start_discharging, midnight, midnight, midnight, 10)
        return True

    except aiohttp.ClientResponseError as e:
        if e.status == 401:
            logging.error("Authentication with the provided details failed. Please check your username and password")
        else:
            logging.error(e)
        return False

    except Exception as e:
        logging.error(e)
        return False


def main():
    # make sure script is being run from the correct directory for cron
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    logging.basicConfig(level=logging.INFO, filename="ESS.log")

    global config
    config = load_config()
    validate_config()

    # Request prices for the next day
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    url_date = tomorrow.strftime('%Y-%m-%d')

    s = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    s.mount('http://', HTTPAdapter(max_retries=retries))
    response = s.get('https://www.ote-cr.cz/en/short-term-markets/electricity/day-ahead-market/@@chart-data?report_date=' + url_date)

    # Extract the prices from the json
    prices = get_prices_from_json(response.json())

    charge_to_full = config["charge_to_full"]
    start_charging_index, mean_min_price = find_cheapest_night_charging(prices, charge_to_full)
    stop_charging_index = start_charging_index + charge_to_full
    start_discharging_index, mean_max_price = find_best_morning_discharging(prices, charge_to_full)

    # Make sure price difference makes charging justified
    if mean_min_price * config["price_multiplier"] > mean_max_price:
        logging.warning("Price difference with specified multiplier ({}) is not big enough to justify charging the battery tonight. Mean min price {:.2f} EUR/MWh, mean max price "
                        "{:.2f} EUR/MWh".format(config["price_multiplier"], mean_min_price, mean_max_price))

        asyncio.run(set_charge_and_discharge_hours(False, midnight, midnight, midnight))
        return

    # convert indexes to strings
    start_charging = datetime.time(hour=start_charging_index).strftime("%H:%M")
    stop_charging = datetime.time(hour=stop_charging_index).strftime("%H:%M")

    # make sure charging and discharge hours do not overlap
    if stop_charging_index < start_discharging_index:
        start_discharging = datetime.time(hour=start_discharging_index - 1).strftime("%H:%M")
    else:
        start_discharging = datetime.time(hour=stop_charging_index).strftime("%H:%M")

    logging.info("Setting charging and discharging for {}".format(tomorrow.strftime("%B %d, %Y")))
    logging.info("Battery will be charged between {} and {} at the ({}h) mean price of {:.2f} EUR/MWh".format(start_charging, stop_charging, charge_to_full, mean_min_price))
    logging.info("Battery will start discharging at {} at the ({}h) mean price of {:.2f} EUR/MWh".format(start_discharging, charge_to_full, mean_max_price))

    # Send data to the ESS
    asyncio.run(set_charge_and_discharge_hours(True, start_charging, stop_charging, start_discharging))


if __name__ == "__main__":
    main()
