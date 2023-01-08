import asyncio
import datetime
import json

import aiohttp
import requests
from alphaess.alphaess import alphaess
from bs4 import BeautifulSoup


# get tomorrow's date and change it to us format with dashes
def read_json(file_path):
    with open(file_path, "r") as f:
        return json.load(f)
    
def get_prices(response):
    # Parse the HTML content of the page
    soup = BeautifulSoup(response.content, 'html.parser')

    # Find the table containing the prices
    table = soup.find('table', {'class': 'table report_table'})

    # Extract the prices from the table
    prices = []
    for i, row in enumerate(table.find_all('tr')):
        cells = row.find_all('td')
        if cells and i <= 24:
            # The price is in the second column
            price = cells[0].text.strip().replace(',', '.')
            prices.append(float(price))
    return prices

def find_three_lowest_consecutive(numbers):
    min_mean = max(numbers)
    for i in range(len(numbers) - 20): # 0 - 4AM
        mean = (numbers[i] + numbers[i + 1] + numbers[i + 2]) / 3
        if mean < min_mean:
            min_mean = mean
            min_index = i
    return min_index, min_mean

def find_three_highest_consecutive(numbers):
    max_mean = min(numbers)
    for i in range(len(numbers) - 6): # 0 - 6PM
        mean = (numbers[i] + numbers[i + 1] + numbers[i + 2]) / 3
        if mean > max_mean:
            max_mean = mean
            max_index = i
    return max_index, max_mean

async def set_charge_and_discharge_hours(username: str, password: str, start_charging: str, stop_charging: str, start_discharging: str):
    client: alphaess = alphaess()
    
    try:
        authenticated = await client.authenticate(username=username, password=password)
        if not authenticated:
            print("Authentication failed")
            return
        
        data = await client.getdata()
        if data:
            if "sys_sn" in data[0]:
                serial = data[0]['sys_sn']
    
        # Set the charge and discharge hours
        await client.setbatterycharge(serial, True, start_charging, stop_charging, "00:00", "00:00", 100)
        await client.setbatterydischarge(serial, True, start_discharging, "23:59", "00:00", "00:00", 10)
        print("Successfully set the charge and discharge hours")

    except aiohttp.ClientResponseError as e:
        if e.status == 401:
            print("Authentication Error")
        else:
            print(e)
    except Exception as e:
        print(e)

def main():
    conf = read_json("config.json")
    username = conf["username"]
    password = conf["password"]
    
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    url_date = tomorrow.strftime('%Y-%m-%d')

    # Make a GET request to the website
    response = requests.get('https://www.ote-cr.cz/en/short-term-markets/electricity/day-ahead-market?date=' + url_date)

    prices = get_prices(response)

    start_charging_index, mean_min_price = find_three_lowest_consecutive(prices)
    start_discharging_index, mean_max_price = find_three_highest_consecutive(prices)
    
    # convert start_charging_index to string
    start_charging = datetime.time(hour=start_charging_index).strftime("%H:%M")
    stop_charging = datetime.time(hour=start_charging_index + 3).strftime("%H:%M")
    start_discharging = datetime.time(hour=start_discharging_index).strftime("%H:%M")
    
    print('Setting charging and discharging for {}'.format(tomorrow.strftime("%B %d, %Y")))
    print('Battery will be charged between {} and {} at the (3h) mean price of {:.2f} EUR/MWh'.format(start_charging, stop_charging, mean_min_price))
    print('Battery will start discharging at {} at the (3h) mean price of {:.2f} EUR/MWh'.format(start_discharging, mean_max_price))

    # Send data to the ESS
    asyncio.run(set_charge_and_discharge_hours(username, password, start_charging, stop_charging, start_discharging))

if __name__ == "__main__":
    main()