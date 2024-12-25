import datetime
import logging
import backoff
import httpx
from typing import List, Dict

class PriceFetcher:
    PRICE_URL = 'https://www.ote-cr.cz/en/short-term-markets/electricity/day-ahead-market/@@chart-data?report_date='

    def __init__(self):
        self.timeout = httpx.Timeout(10.0, connect=60.0)

    @backoff.on_exception(backoff.expo,
                          (httpx.RequestError, httpx.HTTPStatusError, httpx.ReadTimeout),
                          max_time=120)
    async def get_request_with_backoff(self, url: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response

    async def fetch_prices_for_date(self, date: datetime.date) -> List[float]:
        url_date = date.strftime('%Y-%m-%d')
        try:
            response = await self.get_request_with_backoff(self.PRICE_URL + url_date)
            return self.get_prices_from_json(response.json())
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logging.error(f"Failed to fetch data from {exc.request.url!r} after retries")
            raise
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
            raise

    @staticmethod
    def get_prices_from_json(prices_json: Dict) -> List[float]:
        return [float(point['y']) for point in prices_json['data']['dataLine'][1]['point']]
