#!/usr/bin/env python3
"""
AlphaESS Charging Optimizer

Optimizes battery charging/discharging based on day-ahead electricity prices.
Dynamically detects valleys and peaks from price data - no hardcoded times.
"""

import argparse
import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

from ote_cr_price_fetcher import PriceFetcher

from battery_manager import BatteryManager
from config import Config
from ess_client import ESSClient
from models import ArbitrageCycle, OptimizationPlan, PriceWindow, SLOTS_PER_DAY
from price_analyzer import PriceAnalyzer
from price_cache import PriceCache

# Configure logging - Lambda uses CloudWatch via stdout, local uses file + stdout
if not logging.getLogger().handlers:
    _is_lambda = os.environ.get('AWS_LAMBDA_FUNCTION_NAME') is not None
    handlers = [logging.StreamHandler()]
    if not _is_lambda:
        handlers.append(logging.FileHandler('logs/ess_optimizer.log'))
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers
    )

logger = logging.getLogger(__name__)


class ESSOptimizer:
    """Dynamic battery optimizer - detects patterns from price data"""

    def __init__(self, config_path: str = "config.yaml"):
        self.config = Config(config_path)

        # Initialize components
        self.ess_client = ESSClient(
            self.config['app_id'],
            self.config['app_secret'],
            self.config['serial_number'],
            int(self.config.get('min_soc', 10)),
            int(self.config.get('max_soc', 100))
        )

        self.battery_manager = BatteryManager(
            charge_hours=float(self.config['charge_to_full']),
            avg_day_load_kw=float(self.config.get('avg_day_load_kw', 1.8)),
            min_soc=int(self.config.get('min_soc', 10)),
            max_soc=int(self.config.get('max_soc', 100))
        )

        self.price_analyzer = PriceAnalyzer(
            price_multiplier=float(self.config['price_multiplier']),
            min_window_slots=int(self.config.get('min_window_slots', 4)),
            smoothing_window=int(self.config.get('smoothing_window', 4))
        )

        self.price_fetcher = PriceFetcher()
        self.price_cache = PriceCache()

        # Store config values for convenience (backward compatibility)
        self.MIN_WINDOW_SLOTS = int(self.config.get('min_window_slots', 4))
        self.DISCHARGE_EXTENSION_THRESHOLD = float(self.config.get('discharge_extension_threshold', 0.85))
        self.price_multiplier = float(self.config['price_multiplier'])
        self.charge_hours = float(self.config['charge_to_full'])

        logger.info("ESS Optimizer initialized")

    # Backward compatibility properties
    @property
    def MIN_SOC(self) -> int:
        return self.battery_manager.MIN_SOC

    @property
    def MAX_SOC(self) -> int:
        return self.battery_manager.MAX_SOC

    @property
    def AVG_DAY_LOAD_KW(self) -> float:
        return self.battery_manager.AVG_DAY_LOAD_KW

    @property
    def battery_capacity_kwh(self) -> Optional[float]:
        return self.battery_manager.battery_capacity_kwh

    @battery_capacity_kwh.setter
    def battery_capacity_kwh(self, value: float):
        self.battery_manager.set_capacity(value)

    # Backward compatibility methods - delegate to components
    def detect_valleys_and_peaks(self, slot_prices: Dict[int, float]):
        return self.price_analyzer.detect_valleys_and_peaks(slot_prices)

    def calculate_charging_slots_needed(self, current_soc: float, target_soc: float = 100) -> int:
        return self.battery_manager.calculate_charging_slots_needed(current_soc, target_soc)

    def estimate_soc_after_discharge(self, current_soc: float, discharge_hours: float) -> float:
        return self.battery_manager.estimate_soc_after_discharge(current_soc, discharge_hours)

    def _validate_discharge_window(self, charge_window: PriceWindow, discharge_window: PriceWindow, start_soc: float) -> bool:
        """Validate that discharge window is long enough to fully discharge what was charged
        
        Args:
            charge_window: The charging window
            discharge_window: The discharge window to validate
            start_soc: Starting SOC percentage before charging
        """
        if not self.battery_manager.battery_capacity_kwh:
            # Can't validate without capacity, allow it
            return True

        # Calculate actual energy that will be charged based on charge duration and starting SOC
        charge_duration_hours = charge_window.duration_hours

        # Calculate SOC change: charge_rate = (MAX_SOC - MIN_SOC) / charge_to_full_hours per hour
        # SOC gained = charge_duration * (100 / charge_to_full_hours)
        soc_gained = (charge_duration_hours / self.battery_manager.charge_hours) * 100

        # Cap at maximum possible (can't charge above MAX_SOC)
        end_soc = min(self.battery_manager.MAX_SOC, start_soc + soc_gained)
        actual_soc_gained = end_soc - start_soc

        # Calculate energy charged in kWh
        capacity_kwh = self.battery_manager.battery_capacity_kwh
        energy_charged_kwh = (actual_soc_gained / 100) * capacity_kwh

        # Calculate discharge time needed based on average load
        discharge_time_needed_hours = energy_charged_kwh / self.battery_manager.AVG_DAY_LOAD_KW

        # Get actual discharge duration
        discharge_duration_hours = discharge_window.duration_hours

        # Allow 5% buffer for rounding
        is_valid = discharge_duration_hours >= discharge_time_needed_hours * 0.95

        if not is_valid:
            logger.info(f"Skipping cycle: discharge window {discharge_window.duration_hours:.2f}h too short "
                        f"for {energy_charged_kwh:.1f} kWh charged (SOC {start_soc:.0f}%→{end_soc:.0f}%, needs {discharge_time_needed_hours:.2f}h)")

        return is_valid

    async def set_charging_schedule(self, enable: bool, period1=None, period2=None) -> bool:
        return await self.ess_client.set_charging_schedule(enable, period1, period2)

    async def set_discharge_schedule(self, enable: bool, period1=None, period2=None) -> bool:
        return await self.ess_client.set_discharge_schedule(enable, period1, period2)

    # Expose internal methods for testing
    def _estimate_consumption_soc_drain(self, hours: float) -> float:
        return self.battery_manager.estimate_consumption_soc_drain(hours)

    def _extend_discharge_window(self, peak: PriceWindow, charge_price: float, slot_prices, max_end_slot=SLOTS_PER_DAY, exclude_slots=None, aggressive_eod=False) -> PriceWindow:
        return self.price_analyzer.extend_discharge_window(
            peak, charge_price, slot_prices, max_end_slot, exclude_slots, aggressive_eod, self.DISCHARGE_EXTENSION_THRESHOLD
        )

    def _find_profitable_discharge_window(self, charge_price: float, slot_prices: Dict[int, float], after_slot: int = 0, exclude_slots=None):
        return self.price_analyzer.find_profitable_discharge_window(charge_price, slot_prices, after_slot, exclude_slots)

    def _create_charge_window(self, valley: PriceWindow, slot_prices: Dict[int, float], slots_needed: Optional[int] = None, is_overnight: bool = False) -> PriceWindow:
        """Create optimal charge window within valley (exposed for testing)
        
        For overnight charging, extends valley if needed to get full charge_to_full hours.
        """
        if slots_needed is None:
            slots_needed = int(self.battery_manager.charge_hours * 4)

        valley_slots = valley.end_slot - valley.start_slot

        # For overnight charging, ensure we get the full charge time by extending valley if needed
        if is_overnight and valley_slots < slots_needed:
            # Try to extend the valley forward/backward to get enough slots
            extended_valley = self._extend_valley_for_overnight_charging(
                valley, slot_prices, slots_needed
            )
            if extended_valley:
                valley = extended_valley
                valley_slots = valley.end_slot - valley.start_slot
                logger.info(f"Extended overnight valley to {valley_slots} slots (needed {slots_needed})")
            else:
                logger.warning(f"⚠️  Cannot extend overnight valley enough: have {valley_slots} slots, need {slots_needed} slots")

        actual_slots = min(slots_needed, valley_slots)
        actual_slots = max(4, actual_slots)

        # Warn if overnight charging won't get full charge
        if is_overnight and actual_slots < slots_needed:
            logger.warning(f"⚠️  Overnight charging window only {actual_slots} slots ({actual_slots / 4:.1f}h), "
                           f"need {slots_needed} slots ({slots_needed / 4:.1f}h) for full charge")

        start, end, avg = self.price_analyzer.find_cheapest_window_in_valley(valley, actual_slots, slot_prices)
        return PriceWindow(start, end, avg, 'valley')

    def _extend_valley_for_overnight_charging(self, valley: PriceWindow, slot_prices: Dict[int, float], slots_needed: int) -> Optional[PriceWindow]:
        """Extend overnight valley to get enough slots for full charging"""
        valley_avg = valley.avg_price
        # Allow extending to prices up to 20% above valley average for overnight charging
        max_price = valley_avg * 1.2

        extended_start = valley.start_slot
        extended_end = valley.end_slot

        # Extend backward first (earlier in night)
        for slot in range(valley.start_slot - 1, max(0, valley.start_slot - 20), -1):
            if slot_prices.get(slot, float('inf')) <= max_price:
                extended_start = slot
            else:
                break

        # Extend forward (later in morning)
        for slot in range(valley.end_slot, min(SLOTS_PER_DAY, valley.end_slot + 20)):
            if slot_prices.get(slot, float('inf')) <= max_price:
                extended_end = slot + 1
            else:
                break

        # Check if we have enough slots now
        if extended_end - extended_start >= slots_needed:
            extended_prices = [slot_prices[s] for s in range(extended_start, extended_end)]
            extended_avg = sum(extended_prices) / len(extended_prices) if extended_prices else valley_avg
            return PriceWindow(extended_start, extended_end, extended_avg, 'valley')

        return None

    async def get_battery_soc(self) -> Optional[float]:
        """Get current battery SOC and update capacity from API"""
        soc = await self.ess_client.get_battery_soc()
        if soc is not None:
            capacity = await self.ess_client.get_battery_capacity()
            if capacity is not None:
                self.battery_manager.set_capacity(capacity)
        return soc

    async def get_prices_for_day(self, target_date: datetime) -> Optional[Dict[int, float]]:
        """Get 15-minute prices for a specific day (cached for today/tomorrow)"""
        try:
            date_obj = target_date.date()
            date_str = str(date_obj)

            # Check cache
            cached = self.price_cache.get(date_str)
            if cached is not None:
                return cached

            # Fetch from API
            prices_list = await self.price_fetcher.fetch_prices_for_date(date_obj, hourly=False)

            if not prices_list or len(prices_list) != SLOTS_PER_DAY:
                logger.error(f"Invalid price data for {date_obj}: got {len(prices_list) if prices_list else 0} values")
                return None

            slot_prices = {slot: price for slot, price in enumerate(prices_list)}

            # Cache if it's today or tomorrow
            self.price_cache.set(date_str, slot_prices)

            return slot_prices
        except Exception as e:
            logger.error(f"Failed to get prices: {e}")
            return None

    def find_arbitrage_cycles(self, valleys: List[PriceWindow], peaks: List[PriceWindow],
                              current_soc: float, slot_prices: Dict[int, float]
                              ) -> List[ArbitrageCycle]:
        """Match valleys with discharge opportunities to create arbitrage cycles"""
        cycles = []
        sorted_valleys = sorted(valleys, key=lambda v: v.start_slot)
        sorted_peaks = sorted(peaks, key=lambda p: p.start_slot)

        used_peaks = set()
        used_discharge_slots = set()  # Track which slots are already used for discharge
        estimated_soc = current_soc
        last_charge_end_slot = 0
        last_discharge_start_slot = 0

        for valley in sorted_valleys:
            next_peak = None
            for peak in sorted_peaks:
                if peak.start_slot >= valley.end_slot and id(peak) not in used_peaks:
                    # Check if this peak overlaps with already used discharge slots
                    peak_slots = set(range(peak.start_slot, peak.end_slot))
                    if len(peak_slots & used_discharge_slots) < len(peak_slots) * 0.5:
                        next_peak = peak
                        break

            if next_peak is None:
                # Find discharge window that doesn't overlap with used slots
                next_peak = self.price_analyzer.find_profitable_discharge_window(
                    valley.avg_price, slot_prices,
                    after_slot=valley.end_slot,
                    exclude_slots=used_discharge_slots
                )
                if next_peak:
                    logger.info(f"Found profitable window from prices: {next_peak}")

            if next_peak is None:
                continue

            spread = next_peak.avg_price - valley.avg_price
            if spread <= 0:
                continue

            # Estimate SOC at the start of this charge window
            # Key insight: If this charge starts BEFORE the previous discharge,
            # the battery is still full from the previous charge
            if valley.start_slot < last_discharge_start_slot and last_charge_end_slot > 0:
                # This charge happens before previous discharge - battery still at 100%
                estimated_soc = 100.0
                # Only account for consumption since last charge ended
                gap_hours = (valley.start_slot - last_charge_end_slot) / 4
                if gap_hours > 0:
                    soc_drain = self.battery_manager.estimate_consumption_soc_drain(gap_hours)
                    estimated_soc = max(self.battery_manager.MIN_SOC, estimated_soc - soc_drain)

                # CRITICAL: If battery is still well-charged (>50%) and we haven't 
                # discharged the previous cycle yet, skip this valley - no point
                # charging an already-charged battery
                if estimated_soc > 50:
                    logger.info(f"Skipping valley {valley}: battery still at {estimated_soc:.0f}% "
                                f"(previous discharge at {last_discharge_start_slot // 4:02d}:{(last_discharge_start_slot % 4) * 15:02d} hasn't happened)")
                    continue
            else:
                # Normal case: account for discharge and consumption
                gap_hours = (valley.start_slot - max(last_charge_end_slot, 0)) / 4
                if gap_hours > 0:
                    soc_drain = self.battery_manager.estimate_consumption_soc_drain(gap_hours)
                    estimated_soc = max(self.battery_manager.MIN_SOC, estimated_soc - soc_drain)

            # Use full valley for overnight or depleted battery
            is_overnight = valley.start_slot < 28

            # Extend discharge, but don't overlap with already used discharge slots
            next_valley_start = SLOTS_PER_DAY
            for v in sorted_valleys:
                if v.start_slot > next_peak.end_slot:
                    next_valley_start = v.start_slot
                    break

            # Also limit extension to not overlap with used discharge windows
            max_end = next_valley_start
            if used_discharge_slots:
                min_used = min(used_discharge_slots)
                # If the peak is before existing discharge, cap extension at that point
                if next_peak.start_slot < min_used:
                    max_end = min(max_end, min_used)

            extended_discharge = self.price_analyzer.extend_discharge_window(
                next_peak, valley.avg_price, slot_prices,
                max_end_slot=max_end,
                exclude_slots=used_discharge_slots,
                aggressive_eod=False,
                discharge_extension_threshold=self.DISCHARGE_EXTENSION_THRESHOLD
            )

            # Skip if extended discharge overlaps too much with existing (>50%)
            extended_slots = set(range(extended_discharge.start_slot, extended_discharge.end_slot))
            overlap = len(extended_slots & used_discharge_slots)
            if overlap > len(extended_slots) * 0.5:
                logger.info(f"Skipping cycle: discharge {extended_discharge} overlaps {overlap} slots with existing")
                continue

            # Create charge window now that discharge is known
            if is_overnight:
                optimal_charge_window = self._create_charge_window(valley, slot_prices, is_overnight=True)
                logger.info(f"Full valley charging (SOC={estimated_soc:.0f}%, overnight={is_overnight})")
            else:
                # Size daytime charge to what can be fully discharged later
                valley_slots = valley.end_slot - valley.start_slot

                if self.battery_manager.battery_capacity_kwh:
                    capacity = self.battery_manager.battery_capacity_kwh
                    discharge_energy_kwh = extended_discharge.duration_hours * self.battery_manager.AVG_DAY_LOAD_KW
                    allowed_soc_gain = (discharge_energy_kwh / capacity) * 100.0
                    soc_headroom = max(0.0, self.battery_manager.MAX_SOC - estimated_soc)
                    soc_gain = max(0.0, min(allowed_soc_gain, soc_headroom))
                    slots_needed = int(round((soc_gain / 100.0) * self.battery_manager.charge_hours * 4))
                else:
                    # Fallback: proportionally size by discharge duration vs full discharge time
                    full_discharge_slots = self.battery_manager.calculate_full_discharge_slots()
                    ratio = (extended_discharge.duration_hours * 4) / full_discharge_slots if full_discharge_slots > 0 else 0
                    slots_needed = int(round(ratio * self.battery_manager.charge_hours * 4))
                slots_needed = max(self.MIN_WINDOW_SLOTS, min(slots_needed, valley_slots))
                if slots_needed <= 0:
                    continue
                optimal_charge_window = self._create_charge_window(valley, slot_prices, slots_needed, is_overnight=False)

            # Validate that discharge window is long enough for day cycles (not overnight)
            if not is_overnight and not self._validate_discharge_window(optimal_charge_window, extended_discharge, estimated_soc):
                continue

            cycles.append(ArbitrageCycle(optimal_charge_window, extended_discharge, spread))
            used_peaks.add(id(next_peak))

            # Track used discharge slots
            for slot in range(extended_discharge.start_slot, extended_discharge.end_slot):
                used_discharge_slots.add(slot)

            last_charge_end_slot = optimal_charge_window.end_slot
            last_discharge_start_slot = extended_discharge.start_slot
            estimated_soc = self.battery_manager.estimate_soc_after_discharge(100, extended_discharge.duration_hours)

            logger.info(f"Cycle {len(cycles)}: {cycles[-1]}")

        # Look for additional profitable discharge windows if we have < 2 cycles
        # This catches evening peaks when morning was already found
        if len(cycles) < 2 and cycles:
            last_cycle = cycles[-1]
            search_start = last_cycle.discharge_window.end_slot + self.MIN_WINDOW_SLOTS

            additional = self.price_analyzer.find_profitable_discharge_window(
                last_cycle.charge_window.avg_price, slot_prices,
                after_slot=search_start, exclude_slots=used_discharge_slots
            )

            if additional:
                # Extend evening windows to EOD (no point saving for tomorrow)
                is_evening = additional.start_slot >= 56  # After 14:00
                additional = self.price_analyzer.extend_discharge_window(
                    additional, last_cycle.charge_window.avg_price, slot_prices,
                    max_end_slot=SLOTS_PER_DAY, aggressive_eod=is_evening,
                    discharge_extension_threshold=self.DISCHARGE_EXTENSION_THRESHOLD
                )

                spread = additional.avg_price - last_cycle.charge_window.avg_price
                if spread > 0:
                    cycles.append(ArbitrageCycle(last_cycle.charge_window, additional, spread))
                    logger.info(f"Found additional discharge: {cycles[-1]}")

        return cycles

    def analyze_day(self, slot_prices: Dict[int, float], current_soc: float) -> OptimizationPlan:
        """Analyze day's prices and create optimization plan"""
        prices = [slot_prices[i] for i in range(SLOTS_PER_DAY)]

        plan = OptimizationPlan(
            date=datetime.now(),
            daily_mean=sum(prices) / len(prices),
            daily_min=min(prices),
            daily_max=max(prices)
        )

        plan.valleys, plan.peaks = self.price_analyzer.detect_valleys_and_peaks(slot_prices)
        plan.cycles = self.find_arbitrage_cycles(plan.valleys, plan.peaks, current_soc, slot_prices)

        # Add discharge windows from cycles to peaks if not already present
        # (cycles may use dynamically found discharge windows not detected as peaks)
        detected_peak_slots = {(p.start_slot, p.end_slot) for p in plan.peaks}
        for cycle in plan.cycles:
            discharge = cycle.discharge_window
            if (discharge.start_slot, discharge.end_slot) not in detected_peak_slots:
                plan.peaks.append(discharge)
                detected_peak_slots.add((discharge.start_slot, discharge.end_slot))

        logger.info(f"Day analysis: mean={plan.daily_mean:.0f}, min={plan.daily_min:.0f}, max={plan.daily_max:.0f}")
        logger.info(f"Found {len(plan.valleys)} valleys: {[str(v) for v in plan.valleys]}")
        logger.info(f"Found {len(plan.peaks)} discharge windows: {[str(p) for p in plan.peaks]}")
        logger.info(f"Profitable cycles: {len(plan.cycles)}")

        return plan

    async def optimize_for_day(self, target_date: datetime, dry_run: bool = False) -> bool:
        """Main optimization for a given day"""
        logger.info(f"{'=' * 50}")
        logger.info(f"🔋 Dynamic optimization for {target_date.date()}" + (" [DRY RUN]" if dry_run else ""))
        logger.info(f"{'=' * 50}")

        # Fetch battery SOC and prices in parallel
        soc_task = self.get_battery_soc()
        prices_task = self.get_prices_for_day(target_date)
        current_soc, slot_prices = await asyncio.gather(soc_task, prices_task)

        if current_soc is None:
            logger.error("Cannot proceed without battery SOC")
            return False

        if not slot_prices:
            logger.error("Cannot proceed without price data")
            return False

        # Analyze day dynamically
        plan = self.analyze_day(slot_prices, current_soc)

        logger.info(f"Daily stats: mean={plan.daily_mean:.0f}, min={plan.daily_min:.0f}, max={plan.daily_max:.0f}")

        if not plan.has_arbitrage_opportunity:
            # No profitable cycles found (no valleys or no profitable discharge windows)
            logger.info("No profitable arbitrage opportunities found - disabling schedules")
            if not dry_run:
                await self.ess_client.set_charging_schedule(False)
                await self.ess_client.set_discharge_schedule(False)
            else:
                logger.info("[DRY RUN] Would disable charging and discharge schedules")
            return True

        # Set up to 2 cycles (API limitation)
        charge_periods = []
        discharge_periods = []

        for cycle in plan.cycles[:2]:  # Max 2 cycles due to API
            charge_periods.append((cycle.charge_window.start_time, cycle.charge_window.end_time))
            discharge_periods.append((cycle.discharge_window.start_time, cycle.discharge_window.end_time))
            logger.info(f"✓ Cycle: {cycle}")

        if dry_run:
            logger.info(f"[DRY RUN] Would set charging schedule: P1={charge_periods[0] if charge_periods else None}, "
                        f"P2={charge_periods[1] if len(charge_periods) > 1 else None}")
            logger.info(f"[DRY RUN] Would set discharge schedule: P1={discharge_periods[0] if discharge_periods else None}, "
                        f"P2={discharge_periods[1] if len(discharge_periods) > 1 else None}")
        else:
            # Set charging schedule
            period1 = charge_periods[0] if len(charge_periods) > 0 else None
            period2 = charge_periods[1] if len(charge_periods) > 1 else None
            await self.ess_client.set_charging_schedule(True, period1, period2)

            # Set discharge schedule
            period1 = discharge_periods[0] if len(discharge_periods) > 0 else None
            period2 = discharge_periods[1] if len(discharge_periods) > 1 else None
            await self.ess_client.set_discharge_schedule(True, period1, period2)

        logger.info(f"Total arbitrage spread: {plan.total_spread:.0f} EUR/MWh")
        return True

    async def run_once(self, target_date: Optional[datetime] = None, dry_run: bool = False):
        """Run optimization once for a given day (defaults to today)"""
        try:
            if target_date is None:
                target_date = datetime.now()
            success = await self.optimize_for_day(target_date, dry_run=dry_run)
            logger.info("✓ Optimization completed" if success else "✗ Optimization failed")
        finally:
            await self.ess_client.close()

    @property
    def client(self):
        """Backward compatibility: expose ess_client as client"""
        return self.ess_client


async def main():
    parser = argparse.ArgumentParser(
        description="AlphaESS Charging Optimizer - Optimizes battery charging/discharging based on day-ahead electricity prices"
    )
    parser.add_argument(
        "--date",
        type=int,
        metavar="DAY",
        help="Day of month (1-31). Optimizes for that day in the current month."
    )

    parser.add_argument(
        "--dry-run",
        "-d",
        action="store_true",
        help="Run in dry run mode without making API changes to schedules."
    )

    args = parser.parse_args()
    optimizer = ESSOptimizer()

    if args.date is not None:
        if not (1 <= args.date <= 31):
            logger.error(f"Invalid date: {args.date}. Must be between 1 and 31.")
            return

        now = datetime.now()
        try:
            target_date = datetime(now.year, now.month, args.date)
            mode = "dry run" if args.dry_run else "live"
            logger.info(f"{mode.capitalize()} mode: Optimizing for {target_date.date()}")
            await optimizer.run_once(target_date, dry_run=args.dry_run)
        except ValueError as e:
            logger.error(f"Invalid date: {e}")
            return
    else:
        # Default: run once for today (expected to be run at midnight)
        await optimizer.run_once()


if __name__ == "__main__":
    asyncio.run(main())
