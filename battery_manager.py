"""Battery state management and calculations"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class BatteryManager:
    """Manages battery state calculations and consumption estimates"""

    def __init__(self, charge_hours: float, avg_day_load_kw: float, min_soc: int, max_soc: int):
        self.charge_hours = charge_hours
        self.AVG_DAY_LOAD_KW = avg_day_load_kw
        self.MIN_SOC = min_soc
        self.MAX_SOC = max_soc
        self.battery_capacity_kwh: Optional[float] = None

    def set_capacity(self, capacity_kwh: float) -> None:
        """Set battery capacity (typically fetched from API)"""
        self.battery_capacity_kwh = capacity_kwh
        logger.debug(f"battery_manager.set_capacity capacity={capacity_kwh:.1f}")

    def calculate_charging_slots_needed(self, current_soc: float, target_soc: float = 100) -> int:
        """Calculate 15-minute slots needed to charge from current to target SOC"""
        soc_gap = max(0, target_soc - current_soc)
        hours_needed = (soc_gap / 100) * self.charge_hours
        slots_needed = int(round(hours_needed * 4))
        return max(1, slots_needed) if soc_gap > 0 else 0

    def estimate_soc_after_discharge(self, current_soc: float, discharge_hours: float) -> float:
        """Estimate SOC after discharging for given hours"""
        capacity = self.battery_capacity_kwh or 15.5  # Fallback if not fetched from API
        kwh_discharged = discharge_hours * self.AVG_DAY_LOAD_KW
        soc_drop = (kwh_discharged / capacity) * 100
        return max(self.MIN_SOC, current_soc - soc_drop)

    def estimate_consumption_soc_drain(self, hours: float) -> float:
        """Estimate SOC drain from household consumption"""
        capacity = self.battery_capacity_kwh or 15.5  # Fallback if not fetched from API
        return (hours * self.AVG_DAY_LOAD_KW / capacity) * 100


    def calculate_full_discharge_slots(self) -> int:
        """Calculate slots needed to fully discharge from max to min SOC"""
        if not self.battery_capacity_kwh:
            return int(self.charge_hours * 4)  # Approximate as symmetric to charge
        usable_kwh = ((self.MAX_SOC - self.MIN_SOC) / 100) * self.battery_capacity_kwh
        hours = usable_kwh / self.AVG_DAY_LOAD_KW
        return int(hours * 4) * 1.2 # add buffer
