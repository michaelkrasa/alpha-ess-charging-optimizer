"""Price analysis and valley/peak detection"""

import logging
from typing import Dict, List, Tuple, Optional

from models import PriceWindow, SLOTS_PER_DAY

logger = logging.getLogger(__name__)


class PriceAnalyzer:
    """Analyzes price data to detect valleys and peaks"""

    def __init__(self, price_multiplier: float, min_window_slots: int = 4, smoothing_window: int = 4):
        self.price_multiplier = price_multiplier
        self.MIN_WINDOW_SLOTS = min_window_slots
        self.SMOOTHING_WINDOW = smoothing_window

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

        peaks = self._find_contiguous_regions(smoothed, prices, peak_threshold, 'peak', below=False)
        absolute_valleys = self._find_contiguous_regions(smoothed, prices, valley_threshold, 'valley', below=True)
        relative_valleys = self._find_valleys_between_peaks(slot_prices, peaks)
        all_valleys = self._merge_valleys(absolute_valleys, relative_valleys)

        logger.debug(f"detect_valleys_and_peaks mean={mean_price:.0f} valley_threshold={valley_threshold:.0f} "
                     f"peak_threshold={peak_threshold:.0f} valleys={len(all_valleys)} peaks={len(peaks)}")
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
                    logger.debug(f"detect_valleys_and_peaks mid_peak_valley={relative_valleys[-1]}")

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

    def _find_contiguous_regions(self, smoothed: List[float], original: List[float], threshold: float, window_type: str, below: bool) -> List[PriceWindow]:
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

    def find_cheapest_window_in_valley(
            self, valley: PriceWindow, slots_needed: int, slot_prices: Dict[int, float]
    ) -> Tuple[int, int, float]:
        """Find the cheapest consecutive N-slot window within a valley.
        
        Returns: (start_slot, end_slot, avg_price)
        """
        valley_start = valley.start_slot
        valley_end = valley.end_slot
        valley_length = valley_end - valley_start

        # Clamp slots_needed to valley length
        actual_slots = min(slots_needed, valley_length)
        actual_slots = max(4, actual_slots)  # Minimum 1 hour

        if actual_slots >= valley_length:
            # Window fills entire valley, no optimization possible
            prices = [slot_prices[s] for s in range(valley_start, valley_end)]
            avg = sum(prices) / len(prices)
            return valley_start, valley_end, avg

        # Slide through valley to find cheapest window
        best_start = valley_start
        best_avg = float('inf')

        for start in range(valley_start, valley_end - actual_slots + 1):
            end = start + actual_slots
            window_prices = [slot_prices[s] for s in range(start, end)]
            avg = sum(window_prices) / len(window_prices)

            if avg < best_avg:
                best_avg = avg
                best_start = start

        best_end = best_start + actual_slots
        logger.debug(f"find_cheapest_window_in_valley start={best_start // 4:02d}:{(best_start % 4) * 15:02d} "
                     f"end={best_end // 4:02d}:{(best_end % 4) * 15:02d} avg={best_avg:.0f} "
                     f"valley={valley.start_time}-{valley.end_time}")

        return best_start, best_end, best_avg

    def find_profitable_discharge_window(
            self, charge_price: float, slot_prices: Dict[int, float],
            after_slot: int = 0, exclude_slots: Optional[set] = None
    ) -> Optional[PriceWindow]:
        """Find profitable discharge window from prices when no peaks detected.
        
        Args:
            exclude_slots: Set of slots already used by other discharge windows
        """
        profit_threshold = charge_price * self.price_multiplier
        best_window, best_avg = None, 0
        in_window, window_start = False, 0
        exclude_slots = exclude_slots or set()

        for slot in range(after_slot, SLOTS_PER_DAY + 1):
            # Skip slots already used by other discharge windows
            if slot in exclude_slots:
                if in_window and slot - window_start >= self.MIN_WINDOW_SLOTS:
                    window_prices = [slot_prices[s] for s in range(window_start, slot) if s not in exclude_slots]
                    if window_prices:
                        avg_price = sum(window_prices) / len(window_prices)
                        if avg_price > best_avg:
                            best_avg = avg_price
                            best_window = PriceWindow(window_start, slot, avg_price, 'peak')
                in_window = False
                continue

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

    def extend_discharge_window(
            self, peak: PriceWindow, charge_price: float,
            slot_prices: Optional[Dict[int, float]], max_end_slot: int = SLOTS_PER_DAY,
            exclude_slots: Optional[set] = None, aggressive_eod: bool = False,
            discharge_extension_threshold: float = 0.85
    ) -> PriceWindow:
        """Extend discharge window to include profitable hours around peak.
        
        Args:
            aggressive_eod: If True, use lower threshold for evening slots (after 20:00)
                           to extend towards EOD even with marginally profitable prices
            exclude_slots: Set of slots already used by other discharge windows
        """
        if slot_prices is None:
            return peak

        exclude_slots = exclude_slots or set()
        profit_threshold = charge_price * self.price_multiplier
        # Lower threshold for late evening - even marginal profit beats wasting charge
        eod_threshold = charge_price * 1.05 if aggressive_eod else profit_threshold
        backward_threshold = peak.avg_price * discharge_extension_threshold

        extended_start = peak.start_slot
        for slot in range(peak.start_slot - 1, -1, -1):
            if slot in exclude_slots:
                break
            slot_price = slot_prices.get(slot, 0)
            if slot_price >= profit_threshold and slot_price >= backward_threshold:
                extended_start = slot
            else:
                break

        # Forward extension
        extended_end = peak.end_slot
        for slot in range(peak.end_slot, min(max_end_slot, SLOTS_PER_DAY)):
            if slot in exclude_slots:
                break
            # After 20:00 (slot 80), use lower threshold if aggressive_eod
            threshold = eod_threshold if slot >= 80 else profit_threshold
            if slot_prices.get(slot, 0) >= threshold:
                extended_end = slot + 1
            else:
                break

        extended_prices = [slot_prices[s] for s in range(extended_start, extended_end)]
        extended_avg = sum(extended_prices) / len(extended_prices) if extended_prices else peak.avg_price

        if extended_start != peak.start_slot or extended_end != peak.end_slot:
            logger.debug(f"extend_discharge_window original={peak.start_time}-{peak.end_time} "
                         f"extended={extended_start // 4:02d}:{(extended_start % 4) * 15:02d}-{extended_end // 4:02d}:{(extended_end % 4) * 15:02d}")

        return PriceWindow(extended_start, extended_end, extended_avg, 'peak')
