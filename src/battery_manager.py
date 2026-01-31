"""Battery state management and calculations"""

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


class BatteryManager:
    """Manages battery state calculations and consumption estimates"""

    # Default battery capacity used when actual capacity is not yet fetched from API
    DEFAULT_CAPACITY_KWH = 15.0

    def __init__(self, charge_rate_kw: float, avg_day_load_kw: float, min_soc: int, max_soc: int):
        self.charge_rate_kw = charge_rate_kw
        self.AVG_DAY_LOAD_KW = avg_day_load_kw
        self.MIN_SOC = min_soc
        self.MAX_SOC = max_soc
        self.battery_capacity_kwh: Optional[float] = None

    @property
    def charge_hours(self) -> float:
        """Calculate hours to charge from 0% to 100% based on charge rate and capacity"""
        capacity = self.battery_capacity_kwh or self.DEFAULT_CAPACITY_KWH
        return capacity / self.charge_rate_kw

    def set_capacity(self, capacity_kwh: float) -> None:
        """Set battery capacity (typically fetched from API)"""
        self.battery_capacity_kwh = capacity_kwh
        logger.debug(f"battery_manager.set_capacity capacity={capacity_kwh:.1f}, charge_hours={self.charge_hours:.2f}")

    def calculate_charging_slots_needed(self, current_soc: float, target_soc: float = 100) -> int:
        """Calculate 15-minute slots needed to charge from current to target SOC.
        
        Always rounds up to the nearest half hour (2 slots) to ensure sufficient charge time.
        """
        soc_gap = max(0, target_soc - current_soc)
        if soc_gap <= 0:
            return 0
        hours_needed = (soc_gap / 100) * self.charge_hours
        slots_raw = hours_needed * 4
        # Round up to nearest half hour (2 slots = 30 min)
        slots_needed = math.ceil(slots_raw / 2) * 2
        return max(2, slots_needed)  # Minimum 2 slots (30 min)

    def calculate_full_charge_slots(self) -> int:
        """Calculate slots needed for a full charge (0% to MAX_SOC).
        
        Always rounds up to the nearest half hour (2 slots).
        """
        hours_needed = (self.MAX_SOC / 100) * self.charge_hours
        slots_raw = hours_needed * 4
        # Round up to nearest half hour (2 slots = 30 min)
        return math.ceil(slots_raw / 2) * 2

    def is_charge_window_sufficient(self, window_slots: int, current_soc: float, target_soc: float = 100) -> bool:
        """Check if a charge window has enough slots to reach target SOC.
        
        Args:
            window_slots: Number of 15-minute slots in the charge window
            current_soc: Current battery SOC percentage
            target_soc: Target SOC percentage (default 100%)
            
        Returns:
            True if window is sufficient, False otherwise
        """
        slots_needed = self.calculate_charging_slots_needed(current_soc, target_soc)
        return window_slots >= slots_needed

    def estimate_soc_after_discharge(self, current_soc: float, discharge_hours: float) -> float:
        """Estimate SOC after discharging for given hours"""
        capacity = self.battery_capacity_kwh or self.DEFAULT_CAPACITY_KWH
        kwh_discharged = discharge_hours * self.AVG_DAY_LOAD_KW
        soc_drop = (kwh_discharged / capacity) * 100
        return max(self.MIN_SOC, current_soc - soc_drop)

    def estimate_consumption_soc_drain(self, hours: float) -> float:
        """Estimate SOC drain from household consumption"""
        capacity = self.battery_capacity_kwh or self.DEFAULT_CAPACITY_KWH
        return (hours * self.AVG_DAY_LOAD_KW / capacity) * 100

    def calculate_full_discharge_slots(self) -> int:
        """Calculate slots needed to fully discharge from max to min SOC"""
        if not self.battery_capacity_kwh:
            return int(self.charge_hours * 4)  # Approximate as symmetric to charge
        usable_kwh = ((self.MAX_SOC - self.MIN_SOC) / 100) * self.battery_capacity_kwh
        hours = usable_kwh / self.AVG_DAY_LOAD_KW
        return int(hours * 4) * 1.2  # add buffer
