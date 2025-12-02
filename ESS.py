#!/usr/bin/env python3
"""
AlphaESS Charging Optimizer - Dynamic Reactive Strategy
Optimizes battery charging/discharging based on day-ahead electricity prices

Strategy:
- Dynamically detect valleys (cheap) and peaks (expensive) from price data
- Create arbitrage opportunities: charge during valleys, discharge during peaks
- Reactive to actual price patterns - no hardcoded times
- Adapts to days with 0, 1, 2, or more peaks
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


@dataclass
class PriceWindow:
    """Represents a price window (valley or peak)"""
    start_slot: int
    end_slot: int
    avg_price: float
    window_type: str  # 'valley' or 'peak'
    
    @property
    def start_time(self) -> str:
        hour = self.start_slot // 4
        minute = (self.start_slot % 4) * 15
        return f"{hour:02d}:{minute:02d}"
    
    @property
    def end_time(self) -> str:
        hour = self.end_slot // 4
        minute = (self.end_slot % 4) * 15
        return f"{hour:02d}:{minute:02d}"
    
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
    spread: float  # discharge_price - charge_price
    
    @property
    def is_profitable(self) -> bool:
        return self.spread > 0
    
    def __repr__(self):
        return (f"Charge {self.charge_window.start_time}-{self.charge_window.end_time} "
                f"@ {self.charge_window.avg_price:.0f} → "
                f"Discharge {self.discharge_window.start_time}-{self.discharge_window.end_time} "
                f"@ {self.discharge_window.avg_price:.0f} | Spread: {self.spread:.0f}")


@dataclass
class OptimizationPlan:
    """Daily optimization plan with dynamically detected cycles"""
    date: datetime
    daily_mean: float
    daily_min: float
    daily_max: float
    
    # Detected windows
    valleys: List[PriceWindow] = field(default_factory=list)
    peaks: List[PriceWindow] = field(default_factory=list)
    
    # Profitable arbitrage cycles
    cycles: List[ArbitrageCycle] = field(default_factory=list)
    
    @property
    def has_arbitrage_opportunity(self) -> bool:
        return len(self.cycles) > 0
    
    @property
    def total_spread(self) -> float:
        return sum(c.spread for c in self.cycles)


class ESSOptimizer:
    """Dynamic reactive battery optimizer - detects patterns from price data"""
    
    def __init__(self, config_path: str = "config.yaml"):
        """Initialize the ESS optimizer"""
        self.config = Config(config_path)
        self.client = alphaess(
            self.config['app_id'],
            self.config['app_secret']
        )
        self.price_fetcher = PriceFetcher()
        self.serial_number = self.config['serial_number']
        self.charge_hours = float(self.config['charge_to_full'])
        self.price_multiplier = float(self.config['price_multiplier'])
        
        # Battery parameters (from config)
        self.CHARGE_RATE_KW = float(self.config.get('charge_rate_kw', 6.0))
        self.AVG_PEAK_LOAD_KW = float(self.config.get('avg_peak_load_kw', 1.8))
        self.MIN_SOC = int(self.config.get('min_soc', 10))
        self.MAX_SOC = int(self.config.get('max_soc', 100))
        
        # Technical parameters (from config)
        self.MIN_WINDOW_SLOTS = int(self.config.get('min_window_slots', 4))
        self.SMOOTHING_WINDOW = int(self.config.get('smoothing_window', 4))
        
        # Battery capacity (fetched from API)
        self.battery_capacity_kwh: Optional[float] = None
        
        # Setup logging
        Path('logs').mkdir(exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('logs/ess_optimizer.log'),
                logging.StreamHandler()
            ]
        )
        logging.info("ESS Optimizer initialized (dynamic mode)")

    # =========================================================================
    # Battery & Price Data
    # =========================================================================
    
    async def get_battery_soc(self) -> Optional[float]:
        """Get current battery state of charge (%) and update battery capacity"""
        try:
            data = await self.client.getdata()
            if isinstance(data, list) and len(data) > 0:
                data = data[0]
            
            # Get SOC from LastPower
            last_power = data.get('LastPower', {})
            soc = float(last_power.get('soc', 0)) if isinstance(last_power, dict) else float(last_power or 0)
            
            # Update battery capacity from API (gross capacity * usable percentage)
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
        """Get 15-minute prices for a specific day (96 slots)"""
        try:
            date_obj = target_date.date()
            prices_list = await self.price_fetcher.fetch_prices_for_date(date_obj, hourly=False)
            
            if not prices_list or len(prices_list) != 96:
                logging.error(f"Invalid price data for {date_obj}: got {len(prices_list) if prices_list else 0} values")
                return None
            
            slot_prices = {slot: price for slot, price in enumerate(prices_list)}
            logging.info(f"Retrieved 96 price slots for {date_obj}")
            return slot_prices
            
        except Exception as e:
            logging.error(f"Failed to get prices: {e}")
            return None

    # =========================================================================
    # Time Conversion Utilities
    # =========================================================================
    
    def slot_to_time(self, slot: int) -> str:
        """Convert slot index (0-95) to HH:MM format"""
        hour = slot // 4
        minute = (slot % 4) * 15
        return f"{hour:02d}:{minute:02d}"

    def time_to_slot(self, hour: int, minute: int = 0) -> int:
        """Convert hour and minute to slot index"""
        return hour * 4 + minute // 15

    def slots_to_time_range(self, start_slot: int, end_slot: int) -> Tuple[str, str]:
        """Convert slot range to time strings (HH:MM format)"""
        return (self.slot_to_time(start_slot), self.slot_to_time(end_slot))

    def hours_to_time_range(self, start_hour: int, end_hour: int) -> Tuple[str, str]:
        """Legacy method for backwards compatibility"""
        return (f"{start_hour:02d}:00", f"{end_hour:02d}:00")

    # =========================================================================
    # SOC & Charging Calculations
    # =========================================================================
    
    def calculate_charging_slots_needed(self, current_soc: float, target_soc: float = 100) -> int:
        """Calculate 15-minute slots needed to charge from current to target SOC"""
        soc_gap = max(0, target_soc - current_soc)
        hours_needed = (soc_gap / 100) * self.charge_hours
        slots_needed = int(round(hours_needed * 4))
        return max(1, slots_needed) if soc_gap > 0 else 0

    def calculate_charging_hours_needed(self, current_soc: float) -> float:
        """Calculate hours needed to charge to 100% from current SOC"""
        return ((100 - current_soc) / 100) * self.charge_hours

    def estimate_soc_after_discharge(self, current_soc: float, discharge_hours: float) -> float:
        """Estimate SOC after discharging for given hours at average peak load"""
        capacity = self.battery_capacity_kwh or 15.5  # fallback if not yet fetched
        kwh_discharged = discharge_hours * self.AVG_PEAK_LOAD_KW
        soc_drop = (kwh_discharged / capacity) * 100
        return max(self.MIN_SOC, current_soc - soc_drop)

    def estimate_soc_needed_for_peak(self, peak_hours: float) -> float:
        """Calculate SOC needed to cover a peak period"""
        capacity = self.battery_capacity_kwh or 15.5  # fallback if not yet fetched
        kwh_needed = peak_hours * self.AVG_PEAK_LOAD_KW
        soc_needed = (kwh_needed / capacity) * 100
        return min(100, soc_needed + self.MIN_SOC)

    # =========================================================================
    # DYNAMIC Peak & Valley Detection
    # =========================================================================
    
    def smooth_prices(self, slot_prices: Dict[int, float]) -> List[float]:
        """Apply moving average smoothing to reduce noise"""
        prices = [slot_prices[i] for i in range(96)]
        smoothed = []
        
        for i in range(96):
            start = max(0, i - self.SMOOTHING_WINDOW // 2)
            end = min(96, i + self.SMOOTHING_WINDOW // 2 + 1)
            smoothed.append(sum(prices[start:end]) / (end - start))
        
        return smoothed

    def detect_valleys_and_peaks(self, slot_prices: Dict[int, float]) -> Tuple[List[PriceWindow], List[PriceWindow]]:
        """
        Dynamically detect valleys (cheap periods) and peaks (expensive periods)
        from the price data itself - NO hardcoded times!
        
        Uses price_multiplier from config:
        - Valley: price < mean / price_multiplier (cheap enough to charge)
        - Peak: price > mean * price_multiplier (expensive enough to discharge)
        
        Returns: (valleys, peaks) as lists of PriceWindow
        """
        prices = [slot_prices[i] for i in range(96)]
        smoothed = self.smooth_prices(slot_prices)
        
        mean_price = sum(prices) / len(prices)
        # Use config's price_multiplier for both thresholds (symmetrical)
        valley_threshold = mean_price / self.price_multiplier  # e.g., mean/1.2 = 83% of mean
        peak_threshold = mean_price * self.price_multiplier    # e.g., mean*1.2 = 120% of mean
        
        logging.info(f"Price analysis: mean={mean_price:.0f}, valley_threshold={valley_threshold:.0f}, peak_threshold={peak_threshold:.0f}")
        
        # First, find peaks (expensive periods)
        peaks = self._find_contiguous_regions(smoothed, prices, peak_threshold, 'peak', below=False)
        
        # Then find valleys - both absolute valleys AND relative dips between peaks
        absolute_valleys = self._find_contiguous_regions(smoothed, prices, valley_threshold, 'valley', below=True)
        
        # Also find local minima between peaks (for mid-day charging opportunities)
        relative_valleys = self._find_valleys_between_peaks(slot_prices, peaks)
        
        # Combine and deduplicate valleys
        all_valleys = self._merge_valleys(absolute_valleys, relative_valleys)
        
        logging.info(f"Detected {len(all_valleys)} valleys (charging opportunities) and {len(peaks)} peaks")
        
        return all_valleys, peaks

    def _find_valleys_between_peaks(self, slot_prices: Dict[int, float], peaks: List[PriceWindow]) -> List[PriceWindow]:
        """Find local minima between consecutive peaks for mid-cycle charging"""
        if len(peaks) < 2:
            return []
        
        relative_valleys = []
        sorted_peaks = sorted(peaks, key=lambda p: p.start_slot)
        
        for i in range(len(sorted_peaks) - 1):
            peak1 = sorted_peaks[i]
            peak2 = sorted_peaks[i + 1]
            
            # Look for cheapest window between these two peaks
            gap_start = peak1.end_slot
            gap_end = peak2.start_slot
            
            if gap_end - gap_start < self.MIN_WINDOW_SLOTS:
                continue  # Gap too small for charging
            
            # Find the cheapest window in this gap (enough for ~2 hours charging)
            charge_slots = min(8, gap_end - gap_start)  # 2 hours max, or whatever fits
            best_start = None
            best_avg = float('inf')
            
            for start in range(gap_start, gap_end - charge_slots + 1):
                end = start + charge_slots
                window_prices = [slot_prices[s] for s in range(start, end)]
                avg = sum(window_prices) / len(window_prices)
                
                if avg < best_avg:
                    best_avg = avg
                    best_start = start
            
            if best_start is not None:
                # Check if this dip is significantly cheaper than the peaks
                peak_avg = (peak1.avg_price + peak2.avg_price) / 2
                if best_avg < peak_avg / self.price_multiplier:  # Cheaper by the configured ratio
                    # Valley must END before next peak starts!
                    valley_end = min(best_start + charge_slots, gap_end)
                    
                    relative_valleys.append(PriceWindow(
                        start_slot=best_start,
                        end_slot=valley_end,
                        avg_price=best_avg,
                        window_type='valley'
                    ))
                    logging.info(f"Found mid-peak valley: {relative_valleys[-1]}")
        
        return relative_valleys

    def _merge_valleys(self, valleys1: List[PriceWindow], valleys2: List[PriceWindow]) -> List[PriceWindow]:
        """Merge two lists of valleys, removing overlaps"""
        all_valleys = valleys1 + valleys2
        if not all_valleys:
            return []
        
        # Sort by start time
        all_valleys.sort(key=lambda v: v.start_slot)
        
        # Remove overlapping valleys (keep the cheaper one)
        merged = [all_valleys[0]]
        for valley in all_valleys[1:]:
            last = merged[-1]
            if valley.start_slot < last.end_slot:
                # Overlapping - keep the cheaper one
                if valley.avg_price < last.avg_price:
                    merged[-1] = valley
            else:
                merged.append(valley)
        
        return merged

    def _find_contiguous_regions(
        self, 
        smoothed: List[float], 
        original: List[float],
        threshold: float, 
        window_type: str,
        below: bool
    ) -> List[PriceWindow]:
        """Find contiguous regions above/below threshold"""
        regions = []
        in_region = False
        region_start = 0
        
        for i in range(96):
            is_in_region = (smoothed[i] < threshold) if below else (smoothed[i] > threshold)
            
            if is_in_region and not in_region:
                # Start of new region
                region_start = i
                in_region = True
            elif not is_in_region and in_region:
                # End of region
                if i - region_start >= self.MIN_WINDOW_SLOTS:
                    avg_price = sum(original[region_start:i]) / (i - region_start)
                    regions.append(PriceWindow(
                        start_slot=region_start,
                        end_slot=i,
                        avg_price=avg_price,
                        window_type=window_type
                    ))
                in_region = False
        
        # Handle region extending to end of day
        if in_region and 96 - region_start >= self.MIN_WINDOW_SLOTS:
            avg_price = sum(original[region_start:96]) / (96 - region_start)
            regions.append(PriceWindow(
                start_slot=region_start,
                end_slot=96,
                avg_price=avg_price,
                window_type=window_type
            ))
        
        return regions

    def find_arbitrage_cycles(
        self, 
        valleys: List[PriceWindow], 
        peaks: List[PriceWindow],
        current_soc: float
    ) -> List[ArbitrageCycle]:
        """
        Match valleys with SEQUENTIAL peaks to create arbitrage cycles
        
        Logic:
        - Valley1 → Peak1 (next peak after valley1)
        - Valley2 → Peak2 (next peak after valley2)
        - This ensures we don't skip peaks (battery won't last all day!)
        - Charge windows are sized to actual battery needs
        """
        cycles = []
        sorted_valleys = sorted(valleys, key=lambda v: v.start_slot)
        sorted_peaks = sorted(peaks, key=lambda p: p.start_slot)
        
        used_peaks = set()
        estimated_soc = current_soc
        
        for valley in sorted_valleys:
            # Find the NEXT peak after this valley (sequential, not best)
            next_peak = None
            for peak in sorted_peaks:
                if peak.start_slot >= valley.end_slot and id(peak) not in used_peaks:
                    next_peak = peak
                    break  # Take the FIRST available peak, not the best
            
            if next_peak is None:
                continue
            
            spread = next_peak.avg_price - valley.avg_price
            
            # Execute arbitrage if there's any positive spread (valley cheaper than peak)
            if spread > 0:
                # Calculate optimal charge window (don't charge longer than needed)
                slots_needed = self.calculate_charging_slots_needed(estimated_soc, 100)
                optimal_charge_window = self._create_optimal_charge_window(valley, slots_needed)
                
                cycles.append(ArbitrageCycle(
                    charge_window=optimal_charge_window,
                    discharge_window=next_peak,
                    spread=spread
                ))
                used_peaks.add(id(next_peak))
                
                # Estimate SOC after this cycle (discharge during peak)
                estimated_soc = self.estimate_soc_after_discharge(100, next_peak.duration_hours)
                
                logging.info(f"Cycle {len(cycles)}: {cycles[-1]}")
        
        return cycles

    def _create_optimal_charge_window(self, valley: PriceWindow, slots_needed: int) -> PriceWindow:
        """Create a charge window of optimal duration within a valley"""
        # Don't charge longer than the valley or longer than needed
        actual_slots = min(slots_needed, valley.end_slot - valley.start_slot)
        actual_slots = max(4, actual_slots)  # At least 1 hour
        
        return PriceWindow(
            start_slot=valley.start_slot,
            end_slot=valley.start_slot + actual_slots,
            avg_price=valley.avg_price,  # Approximate
            window_type='valley'
        )

    # =========================================================================
    # Legacy Window Finding (for backwards compatibility)
    # =========================================================================
    
    def find_cheapest_window(
        self, 
        slot_prices: Dict[int, float], 
        window_slots: int, 
        start_slot: int, 
        end_slot: int
    ) -> Optional[PriceWindow]:
        """Find cheapest consecutive window of given length"""
        window_slots = max(1, window_slots)
        best_start = None
        best_mean_price = float('inf')
        
        for start in range(start_slot, min(end_slot + 1, 96 - window_slots + 1)):
            end = start + window_slots
            if end > 96:
                continue
            
            window_prices = [slot_prices[s] for s in range(start, end) if s in slot_prices]
            if len(window_prices) < window_slots:
                continue
            
            mean_price = sum(window_prices) / len(window_prices)
            if mean_price < best_mean_price:
                best_mean_price = mean_price
                best_start = start
        
        if best_start is None:
            return None
        
        return PriceWindow(
            start_slot=best_start,
            end_slot=best_start + window_slots,
            avg_price=best_mean_price,
            window_type='valley'
        )

    def find_most_expensive_window(
        self, 
        slot_prices: Dict[int, float], 
        window_slots: int, 
        start_slot: int, 
        end_slot: int
    ) -> Optional[PriceWindow]:
        """Find most expensive consecutive window of given length"""
        window_slots = max(1, window_slots)
        best_start = None
        best_mean_price = float('-inf')
        
        for start in range(start_slot, min(end_slot + 1, 96 - window_slots + 1)):
            end = start + window_slots
            if end > 96:
                continue
            
            window_prices = [slot_prices[s] for s in range(start, end) if s in slot_prices]
            if len(window_prices) < window_slots:
                continue
            
            mean_price = sum(window_prices) / len(window_prices)
            if mean_price > best_mean_price:
                best_mean_price = mean_price
                best_start = start
        
        if best_start is None:
            return None
        
        return PriceWindow(
            start_slot=best_start,
            end_slot=best_start + window_slots,
            avg_price=best_mean_price,
            window_type='peak'
        )

    def find_most_expensive_slots(
        self, 
        slot_prices: Dict[int, float], 
        slot_count: int = 12,
        start_slot: int = 0, 
        end_slot: int = 48
    ) -> List[int]:
        """Find N most expensive 15-minute slots in given range"""
        slots_in_range = {s: p for s, p in slot_prices.items() if start_slot <= s <= end_slot}
        if not slots_in_range:
            return []
        
        sorted_slots = sorted(slots_in_range.items(), key=lambda x: x[1], reverse=True)
        return [s for s, p in sorted_slots[:slot_count]]

    def find_most_expensive_hours(
        self, 
        hourly_prices: dict, 
        count: int = 3,
        start_hour: int = 0, 
        end_hour: int = 12
    ) -> List[int]:
        """Find N most expensive hours in given range (legacy)"""
        hours_in_range = {h: p for h, p in hourly_prices.items() if start_hour <= h <= end_hour}
        if not hours_in_range:
            return []
        sorted_hours = sorted(hours_in_range.items(), key=lambda x: x[1], reverse=True)
        return [h for h, p in sorted_hours[:count]]

    # =========================================================================
    # Dynamic Analysis
    # =========================================================================
    
    def analyze_day(self, slot_prices: Dict[int, float], current_soc: float) -> OptimizationPlan:
        """
        Analyze a day's prices and create optimization plan
        DYNAMICALLY detects peaks and valleys - no hardcoded times!
        """
        prices = [slot_prices[i] for i in range(96)]
        daily_mean = sum(prices) / len(prices)
        daily_min = min(prices)
        daily_max = max(prices)
        
        plan = OptimizationPlan(
            date=datetime.now(),
            daily_mean=daily_mean,
            daily_min=daily_min,
            daily_max=daily_max
        )
        
        # Dynamically detect valleys and peaks from the data
        valleys, peaks = self.detect_valleys_and_peaks(slot_prices)
        plan.valleys = valleys
        plan.peaks = peaks
        
        # Find profitable arbitrage cycles (sequential pairing)
        plan.cycles = self.find_arbitrage_cycles(valleys, peaks, current_soc)
        
        # Log summary
        logging.info(f"Day analysis: mean={daily_mean:.0f}, min={daily_min:.0f}, max={daily_max:.0f}")
        logging.info(f"Found {len(valleys)} valleys: {[str(v) for v in valleys]}")
        logging.info(f"Found {len(peaks)} peaks: {[str(p) for p in peaks]}")
        logging.info(f"Profitable cycles: {len(plan.cycles)}")
        
        return plan

    def compare_with_tomorrow(
        self, 
        today_prices: Dict[int, float], 
        tomorrow_prices: Dict[int, float]
    ) -> dict:
        """Compare today's evening prices with tomorrow's outlook"""
        today_prices_list = [today_prices[i] for i in range(96)]
        tomorrow_prices_list = [tomorrow_prices[i] for i in range(96)]
        
        today_mean = sum(today_prices_list) / len(today_prices_list)
        tomorrow_mean = sum(tomorrow_prices_list) / len(tomorrow_prices_list)
        
        # Find valleys in both days dynamically
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

    # =========================================================================
    # API Schedule Control
    # =========================================================================
    
    async def set_charging_schedule(
        self, 
        enable: bool, 
        period1: Optional[Tuple[str, str]] = None,
        period2: Optional[Tuple[str, str]] = None
    ) -> bool:
        """Set battery charging schedule with up to 2 periods"""
        try:
            time1_start, time1_end = period1 if period1 else ("00:00", "00:00")
            time2_start, time2_end = period2 if period2 else ("00:00", "00:00")
            
            result = await self.client.updateChargeConfigInfo(
                sysSn=self.serial_number,
                batHighCap=100,
                gridCharge=1 if enable else 0,
                timeChaf1=time1_start,
                timeChae1=time1_end,
                timeChaf2=time2_start,
                timeChae2=time2_end
            )
            
            if enable:
                msg = f"✓ Charging enabled: P1={time1_start}-{time1_end}"
                if period2 and period2[0] != "00:00":
                    msg += f", P2={time2_start}-{time2_end}"
                logging.info(msg)
            else:
                logging.info("✓ Charging disabled")
            
            return True
        except Exception as e:
            logging.error(f"Failed to set charging schedule: {e}")
            return False

    async def set_discharge_schedule(
        self, 
        enable: bool,
        period1: Optional[Tuple[str, str]] = None,
        period2: Optional[Tuple[str, str]] = None
    ) -> bool:
        """Set battery discharge schedule with up to 2 periods"""
        try:
            time1_start, time1_end = period1 if period1 else ("00:00", "00:00")
            time2_start, time2_end = period2 if period2 else ("00:00", "00:00")
            
            result = await self.client.updateDisChargeConfigInfo(
                sysSn=self.serial_number,
                batUseCap=10,
                ctrDis=1 if enable else 0,
                timeDisf1=time1_start,
                timeDise1=time1_end,
                timeDisf2=time2_start,
                timeDise2=time2_end
            )
            
            if enable:
                msg = f"✓ Discharge enabled: P1={time1_start}-{time1_end}"
                if period2 and period2[0] != "00:00":
                    msg += f", P2={time2_start}-{time2_end}"
                logging.info(msg)
            else:
                logging.info("✓ Discharge disabled")
            
            return True
        except Exception as e:
            logging.error(f"Failed to set discharge schedule: {e}")
            return False

    # =========================================================================
    # Main Optimization Logic
    # =========================================================================
    
    async def optimize_for_day(self, target_date: datetime) -> bool:
        """
        Main dynamic optimization for a given day
        
        Detects valleys and peaks from price data, creates arbitrage cycles,
        and sets charging/discharging schedules accordingly.
        """
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
        logging.info(f"{'='*60}\n")
        return True

    async def reactive_check(self, current_hour: int) -> bool:
        """
        Reactive check - re-analyze current situation
        
        Called periodically to adjust based on current time and SOC
        """
        now = datetime.now()
        current_soc = await self.get_battery_soc()
        
        if current_soc is None:
            return False
        
        logging.info(f"⚡ Reactive check at {current_hour:02d}:00 | SOC: {current_soc:.1f}%")
        
        # Get today's prices
        today_prices = await self.get_prices_for_day(now)
        if not today_prices:
            return False
        
        # Analyze from current slot onwards
        current_slot = current_hour * 4
        
        # Re-analyze remaining day
        plan = self.analyze_day(today_prices, current_soc)
        
        # Filter cycles that are still actionable (start after current time)
        actionable_cycles = [
            c for c in plan.cycles 
            if c.charge_window.start_slot >= current_slot
        ]
        
        if actionable_cycles:
            logging.info(f"Found {len(actionable_cycles)} actionable cycles from now")
            for cycle in actionable_cycles:
                logging.info(f"  → {cycle}")
        else:
            logging.info("No actionable cycles remaining today")
        
        # If it's evening (after 18:00), also check tomorrow
        if current_hour >= 18:
            tomorrow = now + timedelta(days=1)
            tomorrow_prices = await self.get_prices_for_day(tomorrow)
            
            if tomorrow_prices:
                comparison = self.compare_with_tomorrow(today_prices, tomorrow_prices)
                logging.info(f"📊 Tomorrow comparison: {comparison['recommendation'].upper()}")
                
                # Optimize for tomorrow
                await self.optimize_for_day(tomorrow)
        
        return True

    # =========================================================================
    # Run Modes
    # =========================================================================
    
    async def run_once(self):
        """Run optimization once for tomorrow"""
        try:
            tomorrow = datetime.now() + timedelta(days=1)
            success = await self.optimize_for_day(tomorrow)
            
            if success:
                logging.info("✓ Optimization completed successfully")
            else:
                logging.error("✗ Optimization failed")
            
        finally:
            await self.client.close()

    async def run_continuous(self):
        """
        Run continuous optimization with reactive checks every 15 minutes
        """
        last_optimization_hour = -1
        
        try:
            while True:
                now = datetime.now()
                current_hour = now.hour
                
                # Daily optimization at 18:00
                if current_hour == 18 and last_optimization_hour != 18:
                    logging.info("📅 Daily optimization trigger")
                    tomorrow = now + timedelta(days=1)
                    await self.optimize_for_day(tomorrow)
                    last_optimization_hour = 18
                
                # Reactive check every hour
                if now.minute < 15 and current_hour != last_optimization_hour:
                    await self.reactive_check(current_hour)
                    last_optimization_hour = current_hour
                
                # SOC monitoring
                current_soc = await self.get_battery_soc()
                
                if current_soc is not None and current_soc < 20:
                    logging.warning(f"⚠️ Battery critically low ({current_soc}%)")
                    # Re-optimize for today
                    await self.optimize_for_day(now)
                
                # Wait 15 minutes
                await asyncio.sleep(900)
                
        except KeyboardInterrupt:
            logging.info("Stopping optimizer...")
        finally:
            await self.client.close()


async def main():
    """Main entry point"""
    import sys
    
    optimizer = ESSOptimizer()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        logging.info("Running single optimization for tomorrow")
        await optimizer.run_once()
    else:
        logging.info("Starting continuous dynamic monitoring mode")
        await optimizer.run_continuous()


if __name__ == "__main__":
    asyncio.run(main())
