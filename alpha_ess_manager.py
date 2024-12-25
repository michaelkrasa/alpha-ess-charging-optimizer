import logging
import httpx
from alphaess.alphaess import alphaess
from datetime import time

from config import Config

class AlphaESSManager:
    def __init__(self, config: Config):
        self.client = alphaess(config['app_id'], config['app_secret'])
        self.serial_number = config['serial_number']

    async def set_charging_schedule(self, should_charge: bool, start_time1: str, end_time1: str, start_time2: str, end_time2: str) -> bool:
        for attempt in range(3):
            try:
                await self.client.updateChargeConfigInfo(
                    sysSn=self.serial_number, 
                    batHighCap=100, 
                    gridCharge=should_charge, 
                    timeChae1=start_time1, 
                    timeChae2=end_time1, 
                    timeChaf1=start_time2,
                    timeChaf2=end_time2
                )
                logging.info("Successfully sent charging schedule to ESS")
                await self.client.close()
                return True
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    logging.error("Authentication failed. Please check your credentials")
                else:
                    logging.error(f"HTTP error occurred: {e}")
            except Exception as e:
                logging.error(f"An error occurred: {e}")
            logging.warning(f"Attempt {attempt + 1} failed, retrying...")
        
        logging.error("Failed to set charging schedule after 3 attempts")
        await self.client.close()
        return False

    async def get_data(self):
        return await self.client.getdata()
