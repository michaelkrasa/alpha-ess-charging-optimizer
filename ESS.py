import asyncio
import datetime
import logging
import os
import time

import aiohttp
import requests
import yaml
from alphaess.alphaess import alphaess

"""
Check the electricity prices for the next day (usually available after 3PM) on the OTE-CR website and set the night charge and morning discharge hours
to maximize utility. 
"""


def load_config():
    with open(os.path.join(os.path.dirname(__file__), "config.yaml")) as f:
        return yaml.load(f, Loader=yaml.FullLoader)


# Extract the prices from the json
def get_prices_from_json(prices_json):
    prices = []
    for point in prices_json['data']['dataLine'][1]['point']:
        prices.append(float(point['y']))

    return prices


# Find local minimum and charge the battery at that time
def find_three_lowest_consecutive(numbers: list):
    min_mean = max(numbers)
    for i in range(len(numbers) - 18):  # 00:00 - 06:00
        mean = (numbers[i] + numbers[i + 1] + numbers[i + 2]) / 3
        if mean < min_mean:
            min_mean = mean
            min_index = i
    return min_index, min_mean


# Find local maximum and discharge the battery at that time
def find_three_highest_consecutive(numbers: list):
    max_mean = min(numbers)
    for i in range(len(numbers) - 12):  # 00:00 - 12:00
        mean = (numbers[i] + numbers[i + 1] + numbers[i + 2]) / 3
        if mean > max_mean:
            max_mean = mean
            max_index = i
    return max_index, max_mean


async def set_charge_and_discharge_hours(config: dict, should_charge: bool, start_charging: str, stop_charging: str, start_discharging: str):
    for i in range(5):
        success = await authenticate_and_send_to_ess(config, should_charge, start_charging, stop_charging, start_discharging)
        if success:
            break
        logging.warning("Authentication and or sending to ESS failed, retrying in 30 seconds")
        await asyncio.sleep(10)
    if success:
        logging.info("Request successfully sent to ESS\n\n")
    else:
        logging.error("Failed to authenticate or send request to ESS after 5 attempts... giving up\n\n")


async def authenticate_and_send_to_ess(config: dict, should_charge: bool, start_charging: str, stop_charging: str, start_discharging: str) -> bool:
    username = config["username"]
    password = config["password"]
    serial_number = config["serial_number"]

    client: alphaess = alphaess()
    try:
        if not await client.authenticate(username=username, password=password):
            logging.error("Authentication with the provided details failed. Please check your username and password")
            return False

        if serial_number == "your_serial_number":
            data = await client.getdata()
            if not data or "sys_sn" not in data[0]:
                logging.error("Could not get serial number from data")
                return False
            serial_number = data[0]['sys_sn']
            logging.info("Your serial number is %s, consider adding it to the config", serial_number, extra={"color": "yellow"})

        # Set the charge and discharge hours
        await client.setbatterycharge(serial_number, should_charge, start_charging, stop_charging, "00:00", "00:00", 100)
        await client.setbatterydischarge(serial_number, should_charge, start_discharging, "23:59", "00:00", "00:00", 10)
        return True

    except aiohttp.ClientResponseError as e:
        if e.status == 401:
            logging.error("Authentication Error")
        else:
            logging.error(e)
        return False

    except Exception as e:
        logging.error(e)
        return False


# Validate the config file for required fields
def validate(conf: dict):
    if "username" not in conf:
        raise ValueError("Username not found in config")
    if "password" not in conf:
        raise ValueError("Password not found in config")
    if "price_multiplier" not in conf:
        raise ValueError("Price multiplier not found in config")


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    logging.basicConfig(level=logging.INFO, filename="ESS.log")

    conf = load_config()
    validate(conf)

    # Request prices for the next day
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    url_date = tomorrow.strftime('%Y-%m-%d')

    for i in range(5):
        response = requests.get('https://www.ote-cr.cz/en/short-term-markets/electricity/day-ahead-market/@@chart-data?report_date=' + url_date)
        if response.status_code == 200:
            break
        elif i == 4:
            logging.error("Failed to get the prices from the website after 5 attempts... giving up")
            return
        logging.error("Request failed, retrying in 30 seconds, has the data for the requested day been published yet? Status code: {}".format(response.status_code))
        time.sleep(10)

    # Extract the prices from the json
    prices = get_prices_from_json(response.json())

    start_charging_index, mean_min_price = find_three_lowest_consecutive(prices)
    start_discharging_index, mean_max_price = find_three_highest_consecutive(prices)

    price_multiplier = conf["price_multiplier"]
    should_charge = mean_min_price * price_multiplier < mean_max_price
    if not should_charge:
        logging.warning("""Price difference with specified multiplier ({}) is not big enough to justify charging the battery tonight.\n
                        Mean min price {:.2f} EUR/MWh, mean max price {:.2f} EUR/MWh""".format(price_multiplier, mean_min_price, mean_max_price))
        asyncio.run(set_charge_and_discharge_hours(conf, should_charge, "00:00", "00:00", "00:00"))
        return

    # convert start_charging_index to string
    start_charging = datetime.time(hour=start_charging_index).strftime("%H:%M")
    stop_charging = datetime.time(hour=start_charging_index + 3).strftime("%H:%M")
    start_discharging = datetime.time(hour=start_discharging_index - 1).strftime("%H:%M")

    logging.info("Setting charging and discharging for {}".format(tomorrow.strftime("%B %d, %Y")))
    logging.info("Battery will be charged between {} and {} at the (3h) mean price of {:.2f} EUR/MWh".format(start_charging, stop_charging, mean_min_price))
    logging.info("Battery will start discharging at {} at the (3h) mean price of {:.2f} EUR/MWh".format(start_discharging, mean_max_price))

    # Send data to the ESS
    asyncio.run(set_charge_and_discharge_hours(conf, should_charge, start_charging, stop_charging, start_discharging))


if __name__ == "__main__":
    main()
