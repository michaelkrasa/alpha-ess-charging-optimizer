"""AlphaESS API client for battery data and schedule management"""

import logging
import os
from typing import Optional, Tuple

from alphaess.alphaess import alphaess

logger = logging.getLogger(__name__)


class ESSClient:
    """Handles all AlphaESS API interactions"""

    def __init__(self, app_id: str, app_secret: str, serial_number: str, min_soc: int, max_soc: int):
        self.client = alphaess(app_id, app_secret)
        self.serial_number = serial_number
        self.MIN_SOC = min_soc
        self.MAX_SOC = max_soc

    async def get_battery_soc(self) -> Optional[float]:
        """Get current battery SOC from API"""
        try:
            data = await self.client.getdata()
            if isinstance(data, list) and len(data) > 0:
                data = data[0]

            last_power = data.get('LastPower', {})
            soc = float(last_power.get('soc', 0)) if isinstance(last_power, dict) else float(last_power or 0)

            logger.info(f"🔋 Battery SOC: {soc}%")
            return soc
        except Exception as e:
            logger.error(f"Failed to get battery SOC: {e}")
            return None

    async def get_battery_capacity(self) -> Optional[float]:
        """Get battery capacity from API"""
        try:
            data = await self.client.getdata()
            if isinstance(data, list) and len(data) > 0:
                data = data[0]

            gross_capacity = float(data.get('cobat', 0))
            usable_percentage = float(data.get('usCapacity', 100))
            if gross_capacity > 0:
                capacity = gross_capacity * (usable_percentage / 100)
                logger.info(f"📊 Battery capacity: {capacity:.1f} kWh (gross: {gross_capacity:.1f} kWh, usable: {usable_percentage:.0f}%)")
                return capacity
            return None
        except Exception as e:
            logger.error(f"Failed to get battery capacity: {e}")
            return None

    async def set_charging_schedule(self, enable: bool, period1: Optional[Tuple[str, str]] = None, period2: Optional[Tuple[str, str]] = None) -> bool:
        """Set battery charging schedule"""
        try:
            t1_start, t1_end = period1 if period1 else ("00:00", "00:00")
            t2_start, t2_end = period2 if period2 else ("00:00", "00:00")

            await self.client.updateChargeConfigInfo(
                sysSn=self.serial_number, batHighCap=self.MAX_SOC, gridCharge=1 if enable else 0,
                timeChaf1=t1_start, timeChae1=t1_end, timeChaf2=t2_start, timeChae2=t2_end
            )

            if enable:
                logger.info(f"  ✓ Charging enabled:")
                logger.info(f"    P1: {t1_start}-{t1_end}")
                if period2 and period2[0] != "00:00":
                    logger.info(f"    P2: {t2_start}-{t2_end}")
            else:
                logger.info("  ✓ Charging disabled")
            return True
        except Exception as e:
            logger.error(f"Failed to set charging schedule: {e}")
            return False

    async def set_discharge_schedule(self, enable: bool, period1: Optional[Tuple[str, str]] = None, period2: Optional[Tuple[str, str]] = None) -> bool:
        """Set battery discharge schedule"""
        try:
            t1_start, t1_end = period1 if period1 else ("00:00", "00:00")
            t2_start, t2_end = period2 if period2 else ("00:00", "00:00")

            await self.client.updateDisChargeConfigInfo(
                sysSn=self.serial_number, batUseCap=self.MIN_SOC, ctrDis=1 if enable else 0,
                timeDisf1=t1_start, timeDise1=t1_end, timeDisf2=t2_start, timeDise2=t2_end
            )

            if enable:
                logger.info(f"  ✓ Discharge enabled:")
                logger.info(f"    P1: {t1_start}-{t1_end}")
                if period2 and period2[0] != "00:00":
                    logger.info(f"    P2: {t2_start}-{t2_end}")
            else:
                logger.info("  ✓ Discharge disabled")
            return True
        except Exception as e:
            logger.error(f"Failed to set discharge schedule: {e}")
            return False

    async def close(self):
        """Close the API client"""
        await self.client.close()

