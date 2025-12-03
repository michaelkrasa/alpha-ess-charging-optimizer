#!/usr/bin/env python3
"""
AlphaESS Charging Optimizer - Dynamic Reactive Strategy

Optimizes battery charging/discharging based on day-ahead electricity prices.
Dynamically detects valleys and peaks from price data - no hardcoded times.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple, List, Dict

from alphaess.alphaess import alphaess
from ote_cr_price_fetcher import PriceFetcher

from config import Config

SLOTS_PER_DAY = 96


@dataclass
class PriceWindow:
    """A time window with price info (valley or peak)"""
    start_slot: int
    end_slot: int
    avg_price: float
    window_type: str
    
    @property
    def start_time(self) -> str:
        return f"{self.start_slot // 4:02d}:{(self.start_slot % 4) * 15:02d}"
    
    @property
    def end_time(self) -> str:
        return f"{self.end_slot // 4:02d}:{(self.end_slot % 4) * 15:02d}"
    
    @property
    def duration_hours(self) -> float:
        return (self.end_slot - self.start_slot) / 4
    
    def __repr__(self):
        return f"{self.window_type.upper()} {self.start_time}-{self.end_time} @ {self.avg_price:.0f}"


@dataclass
class ArbitrageCycle:
    """A charge→discharge arbitrage opportunity"""
    charge_window: PriceWindow
    discharge_window: PriceWindow
    spread: float
    
    def __repr__(self):
        return (f"Charge {self.charge_window.start_time}-{self.charge_window.end_time} "
                f"@ {self.charge_window.avg_price:.0f} → "
                f"Discharge {self.discharge_window.start_time}-{self.discharge_window.end_time} "
                f"@ {self.discharge_window.avg_price:.0f} | Spread: {self.spread:.0f}")


@dataclass
class OptimizationPlan:
    """Daily optimization plan"""
    date: datetime
    daily_mean: float
    daily_min: float
    daily_max: float
    valleys: List[PriceWindow] = field(default_factory=list)
    peaks: List[PriceWindow] = field(default_factory=list)
    cycles: List[ArbitrageCycle] = field(default_factory=list)
    
    @property
    def has_arbitrage_opportunity(self) -> bool:
        return len(self.cycles) > 0
    
    @property
    def total_spread(self) -> float:
        return sum(c.spread for c in self.cycles)


class ESSOptimizer:
    """Dynamic battery optimizer - detects patterns from price data"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config = Config(config_path)
        self.client = alphaess(self.config['app_id'], self.config['app_secret'])
        self.price_fetcher = PriceFetcher()
        self.serial_number = self.config['serial_number']
        self.charge_hours = float(self.config['charge_to_full'])
        self.price_multiplier = float(self.config['price_multiplier'])
        
        self.CHARGE_RATE_KW = float(self.config.get('charge_rate_kw', 6.0))
        self.AVG_PEAK_LOAD_KW = float(self.config.get('avg_peak_load_kw', 1.8))
        self.MIN_SOC = int(self.config.get('min_soc', 10))
        self.MAX_SOC = int(self.config.get('max_soc', 100))
        self.MIN_WINDOW_SLOTS = int(self.config.get('min_window_slots', 4))
        self.SMOOTHING_WINDOW = int(self.config.get('smoothing_window', 4))
        self.battery_capacity_kwh: Optional[float] = None
        
        Path('logs').mkdir(exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('logs/ess_optimizer.log'),
                logging.StreamHandler()
            ]
        )
        logging.info("ESS Optimizer initialized")

    async def get_battery_soc(self) -> Optional[float]:
        """Get current battery SOC and update capacity from API"""
        try:
            data = await self.client.getdata()
            if isinstance(data, list) and len(data) > 0:
                data = data[0]
            
            last_power = data.get('LastPower', {})
            soc = float(last_power.get('soc', 0)) if isinstance(last_power, dict) else float(last_power or 0)
            
            gross_capacity = float(data.get('cobat', 0))
            usable_percentage = float(data.get('usCapacity', 100))
            if gross_capacity > 0:
                self.battery_capacity_kwh = gross_capacity * (usable_percentage / 100)
                logging.info(f"Battery capacity: {self.battery_capacity_kwh:.1f} kWh "
                           f"(gross: {gross_capacity:.1f} kWh, usable: {usable_percentage:.0f}%)")
            
            logging.info(f"Current battery SOC: {soc}%")
            return soc
        except Exception as e:
            logging.error(f"Failed to get battery SOC: {e}")
            return None

    async def get_prices_for_day(self, target_date: datetime) -> Optional[Dict[int, float]]:
        """Get 15-minute prices for a specific day"""
        try:
            date_obj = target_date.date()
            prices_list = await self.price_fetcher.fetch_prices_for_date(date_obj, hourly=False)
            
            if not prices_list or len(prices_list) != SLOTS_PER_DAY:
                logging.error(f"Invalid price data for {date_obj}: got {len(prices_list) if prices_list else 0} values")
                return None
            
            slot_prices = {slot: price for slot, price in enumerate(prices_list)}
            logging.info(f"Retrieved {SLOTS_PER_DAY} price slots for {date_obj}")
            return slot_prices
        except Exception as e:
            logging.error(f"Failed to get prices: {e}")
            return None

    def calculate_charging_slots_needed(self, current_soc: float, target_soc: float = 100) -> int:
        """Calculate 15-minute slots needed to charge from current to target SOC"""
        soc_gap = max(0, target_soc - current_soc)
        hours_needed = (soc_gap / 100) * self.charge_hours
        slots_needed = int(round(hours_needed * 4))
        return max(1, slots_needed) if soc_gap > 0 else 0

    def estimate_soc_after_discharge(self, current_soc: float, discharge_hours: float) -> float:
        """Estimate SOC after discharging for given hours"""
        capacity = self.battery_capacity_kwh or 15.5
        kwh_discharged = discharge_hours * self.AVG_PEAK_LOAD_KW
        soc_drop = (kwh_discharged / capacity) * 100
        return max(self.MIN_SOC, current_soc - soc_drop)

    def smooth_prices(self, slot_prices: Dict[int, float]) -> List[float]:
        """Apply moving average smoothing to reduce noise"""
        prices = [slot_prices[i] for i in range(SLOTS_PER_DAY)]
        smoothed = []
        for i in range(SLOTS_PER_DAY):
            start = max(0, i - self.SMOOTHING_WINDOW // 2)
            end = min(SLOTS_PER_DAY, i + self.SMOOTHING_WINDOW // 2 + 1)
            smoothed.append(sum(prices[start:end]) / (end - start))
        return smoothed

    def detect_valleys_and_peaks(self, slot_prices: Dict[int, float]) -> Tuple[List[PriceWindow], List[PriceWindow]]:
        """Detect valleys and peaks from price data using price_multiplier thresholds"""
        prices = [slot_prices[i] for i in range(SLOTS_PER_DAY)]
        smoothed = self.smooth_prices(slot_prices)
        
        mean_price = sum(prices) / len(prices)
        valley_threshold = mean_price / self.price_multiplier
        peak_threshold = mean_price * self.price_multiplier
        
        logging.info(f"Price analysis: mean={mean_price:.0f}, valley_threshold={valley_threshold:.0f}, peak_threshold={peak_threshold:.0f}")
        
        peaks = self._find_contiguous_regions(smoothed, prices, peak_threshold, 'peak', below=False)
        absolute_valleys = self._find_contiguous_regions(smoothed, prices, valley_threshold, 'valley', below=True)
        relative_valleys = self._find_valleys_between_peaks(slot_prices, peaks)
        all_valleys = self._merge_valleys(absolute_valleys, relative_valleys)
        
        logging.info(f"Detected {len(all_valleys)} valleys and {len(peaks)} peaks")
        return all_valleys, peaks

    def _find_valleys_between_peaks(self, slot_prices: Dict[int, float], peaks: List[PriceWindow]) -> List[PriceWindow]:
        """Find local minima between consecutive peaks for mid-cycle charging"""
        if len(peaks) < 2:
            return []
        
        relative_valleys = []
        sorted_peaks = sorted(peaks, key=lambda p: p.start_slot)
        
        for i in range(len(sorted_peaks) - 1):
            peak1, peak2 = sorted_peaks[i], sorted_peaks[i + 1]
            gap_start, gap_end = peak1.end_slot, peak2.start_slot
            
            if gap_end - gap_start < self.MIN_WINDOW_SLOTS:
                continue
            
            charge_slots = min(8, gap_end - gap_start)
            best_start, best_avg = None, float('inf')
            
            for start in range(gap_start, gap_end - charge_slots + 1):
                window_prices = [slot_prices[s] for s in range(start, start + charge_slots)]
                avg = sum(window_prices) / len(window_prices)
                if avg < best_avg:
                    best_avg, best_start = avg, start
            
            if best_start is not None:
                peak_avg = (peak1.avg_price + peak2.avg_price) / 2
                if best_avg < peak_avg / self.price_multiplier:
                    valley_end = min(best_start + charge_slots, gap_end)
                    relative_valleys.append(PriceWindow(
                        start_slot=best_start, end_slot=valley_end,
                        avg_price=best_avg, window_type='valley'
                    ))
                    logging.info(f"Found mid-peak valley: {relative_valleys[-1]}")
        
        return relative_valleys

    def _merge_valleys(self, valleys1: List[PriceWindow], valleys2: List[PriceWindow]) -> List[PriceWindow]:
        """Merge valley lists, keeping cheaper one on overlap"""
        all_valleys = valleys1 + valleys2
        if not all_valleys:
            return []
        
        all_valleys.sort(key=lambda v: v.start_slot)
        merged = [all_valleys[0]]
        
        for valley in all_valleys[1:]:
            last = merged[-1]
            if valley.start_slot < last.end_slot:
                if valley.avg_price < last.avg_price:
                    merged[-1] = valley
            else:
                merged.append(valley)
        
        return merged

    def _find_contiguous_regions(
        self, smoothed: List[float], original: List[float],
        threshold: float, window_type: str, below: bool
    ) -> List[PriceWindow]:
        """Find contiguous regions above/below threshold"""
        regions = []
        in_region = False
        region_start = 0
        
        for i in range(SLOTS_PER_DAY):
            is_in_region = (smoothed[i] < threshold) if below else (smoothed[i] > threshold)
            
            if is_in_region and not in_region:
                region_start = i
                in_region = True
            elif not is_in_region and in_region:
                if i - region_start >= self.MIN_WINDOW_SLOTS:
                    avg_price = sum(original[region_start:i]) / (i - region_start)
                    regions.append(PriceWindow(region_start, i, avg_price, window_type))
                in_region = False
        
        if in_region and SLOTS_PER_DAY - region_start >= self.MIN_WINDOW_SLOTS:
            avg_price = sum(original[region_start:SLOTS_PER_DAY]) / (SLOTS_PER_DAY - region_start)
            regions.append(PriceWindow(region_start, SLOTS_PER_DAY, avg_price, window_type))
        
        return regions

    def find_arbitrage_cycles(
        self, valleys: List[PriceWindow], peaks: List[PriceWindow],
        current_soc: float, slot_prices: Optional[Dict[int, float]] = None
    ) -> List[ArbitrageCycle]:
        """Match valleys with discharge opportunities to create arbitrage cycles"""
        cycles = []
        sorted_valleys = sorted(valleys, key=lambda v: v.start_slot)
        sorted_peaks = sorted(peaks, key=lambda p: p.start_slot)
        
        used_peaks = set()
        estimated_soc = current_soc
        last_window_end_slot = 0
        
        for valley in sorted_valleys:
            next_peak = None
            for peak in sorted_peaks:
                if peak.start_slot >= valley.end_slot and id(peak) not in used_peaks:
                    next_peak = peak
                    break
            
            if next_peak is None and slot_prices:
                next_peak = self._find_profitable_discharge_window(
                    valley.avg_price, slot_prices, after_slot=valley.end_slot
                )
                if next_peak:
                    logging.info(f"Found profitable window from prices: {next_peak}")
            
            if next_peak is None:
                continue
            
            spread = next_peak.avg_price - valley.avg_price
            if spread <= 0:
                continue
            
            # Account for consumption between windows
            gap_hours = (valley.start_slot - last_window_end_slot) / 4
            if gap_hours > 0:
                soc_drain = self._estimate_consumption_soc_drain(gap_hours)
                estimated_soc = max(self.MIN_SOC, estimated_soc - soc_drain)
            
            # Use full valley for overnight or depleted battery
            is_overnight = valley.start_slot < 28
            if is_overnight or estimated_soc < 30:
                optimal_charge_window = self._create_full_valley_charge_window(valley)
                logging.info(f"Full valley charging (SOC={estimated_soc:.0f}%, overnight={is_overnight})")
            else:
                slots_needed = self.calculate_charging_slots_needed(estimated_soc, 100)
                optimal_charge_window = self._create_optimal_charge_window(valley, slots_needed)
            
            # Extend discharge to cover all profitable hours
            next_valley_start = SLOTS_PER_DAY
            for v in sorted_valleys:
                if v.start_slot > next_peak.end_slot:
                    next_valley_start = v.start_slot
                    break
            
            extended_discharge = self._extend_discharge_window(
                next_peak, valley.avg_price, slot_prices, max_end_slot=next_valley_start
            )
            
            cycles.append(ArbitrageCycle(optimal_charge_window, extended_discharge, spread))
            used_peaks.add(id(next_peak))
            last_window_end_slot = extended_discharge.end_slot
            estimated_soc = self.estimate_soc_after_discharge(100, extended_discharge.duration_hours)
            
            logging.info(f"Cycle {len(cycles)}: {cycles[-1]}")
        
        return cycles
    
    def _find_profitable_discharge_window(
        self, charge_price: float, slot_prices: Dict[int, float], after_slot: int = 0
    ) -> Optional[PriceWindow]:
        """Find profitable discharge window from prices when no peaks detected"""
        profit_threshold = charge_price * 1.2
        best_window, best_avg = None, 0
        in_window, window_start = False, 0
        
        for slot in range(after_slot, SLOTS_PER_DAY + 1):
            is_profitable = slot < SLOTS_PER_DAY and slot_prices.get(slot, 0) >= profit_threshold
            
            if is_profitable and not in_window:
                window_start = slot
                in_window = True
            elif not is_profitable and in_window:
                if slot - window_start >= self.MIN_WINDOW_SLOTS:
                    window_prices = [slot_prices[s] for s in range(window_start, slot)]
                    avg_price = sum(window_prices) / len(window_prices)
                    if avg_price > best_avg:
                        best_avg = avg_price
                        best_window = PriceWindow(window_start, slot, avg_price, 'peak')
                in_window = False
        
        return best_window
    
    def _extend_discharge_window(
        self, peak: PriceWindow, charge_price: float,
        slot_prices: Optional[Dict[int, float]], max_end_slot: int = SLOTS_PER_DAY
    ) -> PriceWindow:
        """Extend discharge window to include all profitable hours around peak"""
        if slot_prices is None:
            return peak
        
        profit_threshold = charge_price * 1.2
        
        extended_start = peak.start_slot
        for slot in range(peak.start_slot - 1, -1, -1):
            if slot_prices.get(slot, 0) >= profit_threshold:
                extended_start = slot
            else:
                break
        
        extended_end = peak.end_slot
        for slot in range(peak.end_slot, min(max_end_slot, SLOTS_PER_DAY)):
            if slot_prices.get(slot, 0) >= profit_threshold:
                extended_end = slot + 1
            else:
                break
        
        extended_prices = [slot_prices[s] for s in range(extended_start, extended_end)]
        extended_avg = sum(extended_prices) / len(extended_prices) if extended_prices else peak.avg_price
        
        if extended_start != peak.start_slot or extended_end != peak.end_slot:
            logging.info(f"Extended discharge: {peak.start_time}-{peak.end_time} → "
                        f"{extended_start//4:02d}:{(extended_start%4)*15:02d}-{extended_end//4:02d}:{(extended_end%4)*15:02d}")
        
        return PriceWindow(extended_start, extended_end, extended_avg, 'peak')
    
    def _estimate_consumption_soc_drain(self, hours: float) -> float:
        """Estimate SOC drain from household consumption"""
        capacity = self.battery_capacity_kwh or 15.5
        avg_consumption_kw = float(self.config.get('avg_overnight_load_kw', 0.5))
        return (hours * avg_consumption_kw / capacity) * 100
    
    def _create_full_valley_charge_window(self, valley: PriceWindow) -> PriceWindow:
        """Use full valley duration for charging (depleted battery)"""
        max_charge_slots = int(self.charge_hours * 4 * 1.2)
        actual_slots = min(valley.end_slot - valley.start_slot, max_charge_slots)
        actual_slots = max(4, actual_slots)
        return PriceWindow(valley.start_slot, valley.start_slot + actual_slots, valley.avg_price, 'valley')

    def _create_optimal_charge_window(self, valley: PriceWindow, slots_needed: int) -> PriceWindow:
        """Create charge window of optimal duration within valley"""
        actual_slots = min(slots_needed, valley.end_slot - valley.start_slot)
        actual_slots = max(4, actual_slots)
        return PriceWindow(valley.start_slot, valley.start_slot + actual_slots, valley.avg_price, 'valley')

    def analyze_day(self, slot_prices: Dict[int, float], current_soc: float) -> OptimizationPlan:
        """Analyze day's prices and create optimization plan"""
        prices = [slot_prices[i] for i in range(SLOTS_PER_DAY)]
        
        plan = OptimizationPlan(
            date=datetime.now(),
            daily_mean=sum(prices) / len(prices),
            daily_min=min(prices),
            daily_max=max(prices)
        )
        
        plan.valleys, plan.peaks = self.detect_valleys_and_peaks(slot_prices)
        plan.cycles = self.find_arbitrage_cycles(plan.valleys, plan.peaks, current_soc, slot_prices)
        
        logging.info(f"Day analysis: mean={plan.daily_mean:.0f}, min={plan.daily_min:.0f}, max={plan.daily_max:.0f}")
        logging.info(f"Found {len(plan.valleys)} valleys: {[str(v) for v in plan.valleys]}")
        logging.info(f"Found {len(plan.peaks)} peaks: {[str(p) for p in plan.peaks]}")
        logging.info(f"Profitable cycles: {len(plan.cycles)}")
        
        return plan

    def compare_with_tomorrow(self, today_prices: Dict[int, float], tomorrow_prices: Dict[int, float]) -> dict:
        """Compare today's prices with tomorrow's outlook"""
        today_mean = sum(today_prices[i] for i in range(SLOTS_PER_DAY)) / SLOTS_PER_DAY
        tomorrow_mean = sum(tomorrow_prices[i] for i in range(SLOTS_PER_DAY)) / SLOTS_PER_DAY
        
        today_valleys, _ = self.detect_valleys_and_peaks(today_prices)
        tomorrow_valleys, _ = self.detect_valleys_and_peaks(tomorrow_prices)
        
        today_valley_avg = min(v.avg_price for v in today_valleys) if today_valleys else today_mean
        tomorrow_valley_avg = min(v.avg_price for v in tomorrow_valleys) if tomorrow_valleys else tomorrow_mean
        
        return {
            'today_mean': today_mean,
            'tomorrow_mean': tomorrow_mean,
            'today_best_valley': today_valley_avg,
            'tomorrow_best_valley': tomorrow_valley_avg,
            'tomorrow_cheaper': tomorrow_valley_avg < today_valley_avg * 0.95,
            'recommendation': 'wait' if tomorrow_valley_avg < today_valley_avg * 0.95 else 'charge_now'
        }

    async def set_charging_schedule(
        self, enable: bool, period1: Optional[Tuple[str, str]] = None, period2: Optional[Tuple[str, str]] = None
    ) -> bool:
        """Set battery charging schedule"""
        try:
            t1_start, t1_end = period1 if period1 else ("00:00", "00:00")
            t2_start, t2_end = period2 if period2 else ("00:00", "00:00")
            
            await self.client.updateChargeConfigInfo(
                sysSn=self.serial_number, batHighCap=100, gridCharge=1 if enable else 0,
                timeChaf1=t1_start, timeChae1=t1_end, timeChaf2=t2_start, timeChae2=t2_end
            )
            
            if enable:
                msg = f"✓ Charging enabled: P1={t1_start}-{t1_end}"
                if period2 and period2[0] != "00:00":
                    msg += f", P2={t2_start}-{t2_end}"
                logging.info(msg)
            else:
                logging.info("✓ Charging disabled")
            return True
        except Exception as e:
            logging.error(f"Failed to set charging schedule: {e}")
            return False

    async def set_discharge_schedule(
        self, enable: bool, period1: Optional[Tuple[str, str]] = None, period2: Optional[Tuple[str, str]] = None
    ) -> bool:
        """Set battery discharge schedule"""
        try:
            t1_start, t1_end = period1 if period1 else ("00:00", "00:00")
            t2_start, t2_end = period2 if period2 else ("00:00", "00:00")
            
            await self.client.updateDisChargeConfigInfo(
                sysSn=self.serial_number, batUseCap=10, ctrDis=1 if enable else 0,
                timeDisf1=t1_start, timeDise1=t1_end, timeDisf2=t2_start, timeDise2=t2_end
            )
            
            if enable:
                msg = f"✓ Discharge enabled: P1={t1_start}-{t1_end}"
                if period2 and period2[0] != "00:00":
                    msg += f", P2={t2_start}-{t2_end}"
                logging.info(msg)
            else:
                logging.info("✓ Discharge disabled")
            return True
        except Exception as e:
            logging.error(f"Failed to set discharge schedule: {e}")
            return False

    async def optimize_for_day(self, target_date: datetime) -> bool:
        """Main optimization for a given day"""
        logging.info(f"\n{'='*60}")
        logging.info(f"🔋 Dynamic optimization for {target_date.date()}")
        logging.info(f"{'='*60}")
        
        # Get current battery SOC
        current_soc = await self.get_battery_soc()
        if current_soc is None:
            logging.error("Cannot proceed without battery SOC")
            return False
        
        # Get prices
        slot_prices = await self.get_prices_for_day(target_date)
        if not slot_prices:
            logging.error("Cannot proceed without price data")
            return False
        
        # Analyze day dynamically
        plan = self.analyze_day(slot_prices, current_soc)
        
        logging.info(f"Daily stats: mean={plan.daily_mean:.0f}, min={plan.daily_min:.0f}, max={plan.daily_max:.0f}")
        
        if not plan.has_arbitrage_opportunity:
            # No profitable cycles found (no valleys or no profitable discharge windows)
            logging.info("No profitable arbitrage opportunities found - disabling schedules")
            await self.set_charging_schedule(False)
            await self.set_discharge_schedule(False)
            return True
        
        # Set up to 2 cycles (API limitation)
        charge_periods = []
        discharge_periods = []
        
        for cycle in plan.cycles[:2]:  # Max 2 cycles due to API
            charge_periods.append((cycle.charge_window.start_time, cycle.charge_window.end_time))
            discharge_periods.append((cycle.discharge_window.start_time, cycle.discharge_window.end_time))
            logging.info(f"✓ Cycle: {cycle}")
        
        # Set charging schedule
        period1 = charge_periods[0] if len(charge_periods) > 0 else None
        period2 = charge_periods[1] if len(charge_periods) > 1 else None
        await self.set_charging_schedule(True, period1, period2)
        
        # Set discharge schedule
        period1 = discharge_periods[0] if len(discharge_periods) > 0 else None
        period2 = discharge_periods[1] if len(discharge_periods) > 1 else None
        await self.set_discharge_schedule(True, period1, period2)
        
        logging.info(f"Total arbitrage spread: {plan.total_spread:.0f} EUR/MWh")
        return True

    async def reactive_check(self, current_hour: int) -> bool:
        """Periodic re-analysis based on current time and SOC"""
        now = datetime.now()
        current_soc = await self.get_battery_soc()
        if current_soc is None:
            return False
        
        logging.info(f"⚡ Reactive check at {current_hour:02d}:00 | SOC: {current_soc:.1f}%")
        
        today_prices = await self.get_prices_for_day(now)
        if not today_prices:
            return False
        
        current_slot = current_hour * 4
        plan = self.analyze_day(today_prices, current_soc)
        
        actionable_cycles = [c for c in plan.cycles if c.charge_window.start_slot >= current_slot]
        if actionable_cycles:
            logging.info(f"Found {len(actionable_cycles)} actionable cycles")
            for cycle in actionable_cycles:
                logging.info(f"  → {cycle}")
        else:
            logging.info("No actionable cycles remaining today")
        
        if current_hour >= 18:
            tomorrow = now + timedelta(days=1)
            tomorrow_prices = await self.get_prices_for_day(tomorrow)
            if tomorrow_prices:
                comparison = self.compare_with_tomorrow(today_prices, tomorrow_prices)
                logging.info(f"📊 Tomorrow: {comparison['recommendation'].upper()}")
                await self.optimize_for_day(tomorrow)
        
        return True

    async def run_once(self):
        """Run optimization once for tomorrow"""
        try:
            tomorrow = datetime.now() + timedelta(days=1)
            success = await self.optimize_for_day(tomorrow)
            logging.info("✓ Optimization completed" if success else "✗ Optimization failed")
        finally:
            await self.client.close()

    async def run_continuous(self):
        """Run continuous optimization with periodic checks"""
        last_optimization_hour = -1
        
        try:
            while True:
                now = datetime.now()
                current_hour = now.hour
                
                if current_hour == 18 and last_optimization_hour != 18:
                    logging.info("📅 Daily optimization trigger")
                    await self.optimize_for_day(now + timedelta(days=1))
                    last_optimization_hour = 18
                
                if now.minute < 15 and current_hour != last_optimization_hour:
                    await self.reactive_check(current_hour)
                    last_optimization_hour = current_hour
                
                current_soc = await self.get_battery_soc()
                if current_soc is not None and current_soc < 20:
                    logging.warning(f"⚠️ Battery low ({current_soc}%)")
                    await self.optimize_for_day(now)
                
                await asyncio.sleep(900)
        except KeyboardInterrupt:
            logging.info("Stopping optimizer...")
        finally:
            await self.client.close()


async def main():
    import sys
    optimizer = ESSOptimizer()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        await optimizer.run_once()
    else:
        await optimizer.run_continuous()


if __name__ == "__main__":
    asyncio.run(main())
