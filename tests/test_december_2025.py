"""
Comprehensive tests for December 2025 real price data.

Tests the optimizer against actual price data for all days in December 2025,
validating that all rules are followed: no overlaps, proper spread, etc.
"""

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import SLOTS_PER_DAY
from src.optimizer import ESSOptimizer

# Directory containing price data
DATA_DIR = Path(__file__).parent / 'test_data' / 'december_2025'


def discover_price_files():
    """Discover all available price data files."""
    if not DATA_DIR.exists():
        return []
    
    files = sorted(DATA_DIR.glob('2025-12-*.json'))
    return [f.stem for f in files]  # Return date strings like '2025-12-01'


def load_prices_for_date(date_str: str) -> dict[int, float] | None:
    """Load price data from JSON file."""
    file_path = DATA_DIR / f"{date_str}.json"
    
    if not file_path.exists():
        return None
    
    try:
        with open(file_path, 'r') as f:
            prices_list = json.load(f)
        
        if not prices_list or len(prices_list) != SLOTS_PER_DAY:
            return None
        
        return {slot: price for slot, price in enumerate(prices_list)}
    except Exception:
        return None


@pytest.fixture
def optimizer():
    """Create an optimizer instance with mocked config matching real config.yaml."""
    with patch('src.optimizer.Config') as mock_config, \
            patch('src.ess_client.alphaess') as mock_client, \
            patch('src.optimizer.PriceFetcher') as mock_fetcher:
        # Mock config with values matching real config.yaml
        config_values = {
            'app_id': 'test_id',
            'app_secret': 'test_secret',
            'serial_number': 'TEST123',
            'charge_to_full': 3.0,
            'price_multiplier': 1.18,  # Matches real config
            'avg_day_load_kw': 1.8,
            'min_soc': 10,
            'max_soc': 100,
            'smoothing_window': 2,
            'min_window_slots': 2,
            'discharge_extension_threshold': 0.8,
            'early_morning_end_hour': 5,
            'afternoon_start_hour': 10,
            'afternoon_end_hour': 18,
            'min_discharge_fraction': 0.5,
            'min_discharge_hours': 1.5,
            'timezone': 'Europe/Prague',
        }
        mock_config_instance = MagicMock()
        mock_config_instance.__getitem__ = lambda self, key: config_values[key]
        mock_config_instance.get = lambda key, default=None: config_values.get(key, default)
        mock_config.return_value = mock_config_instance

        # Mock client
        mock_client_instance = MagicMock()
        mock_client_instance.close = AsyncMock()
        mock_client.return_value = mock_client_instance

        # Mock price fetcher
        mock_fetcher_instance = MagicMock()
        mock_fetcher.return_value = mock_fetcher_instance

        opt = ESSOptimizer()
        # Set battery capacity (normally fetched from API)
        opt.battery_capacity_kwh = 15.5
        return opt


def is_overnight(charge_window) -> bool:
    """Check if charge window is overnight (starts before 7:00, slot 28)."""
    return charge_window.start_slot < 28


# Discover available test data files
AVAILABLE_DATES = discover_price_files()


@pytest.mark.skipif(
    len(AVAILABLE_DATES) == 0,
    reason="No price data files found. Run fetch_december_prices.py first."
)
@pytest.mark.parametrize("date_str", AVAILABLE_DATES)
class TestDecember2025DailyValidation:
    """Comprehensive validation tests for each day in December 2025."""

    def test_no_charge_overlap(self, optimizer, date_str):
        """Verify that charge windows do not have partial overlap.
        
        Note: It's valid to have the EXACT SAME charge window for multiple discharge cycles
        (one charge → two discharges). But different charge windows that partially overlap is a bug.
        """
        prices_dict = load_prices_for_date(date_str)
        if prices_dict is None:
            pytest.skip(f"No price data available for {date_str}")
        
        plan = optimizer.analyze_day(prices_dict, current_soc=30.0)
        
        if not plan.cycles:
            pytest.skip(f"No cycles found for {date_str} - may not have arbitrage opportunity")
        
        # Collect unique charge windows (by start/end slot)
        seen_windows = {}  # (start, end) -> first cycle index
        for i, cycle in enumerate(plan.cycles):
            window_key = (cycle.charge_window.start_slot, cycle.charge_window.end_slot)
            
            if window_key in seen_windows:
                # Exact same window reused - this is valid (one charge → multiple discharges)
                continue
            
            # Check for partial overlap with previously seen windows
            cycle_charge = set(range(cycle.charge_window.start_slot, cycle.charge_window.end_slot))
            for (prev_start, prev_end), prev_idx in seen_windows.items():
                prev_slots = set(range(prev_start, prev_end))
                overlap = cycle_charge & prev_slots
                
                if overlap:
                    # There's overlap, but is it exact reuse or partial overlap?
                    if window_key != (prev_start, prev_end):
                        assert False, \
                            f"{date_str}: Charge window {i+1} ({cycle.charge_window.start_time}-{cycle.charge_window.end_time}) " \
                            f"partially overlaps with cycle {prev_idx+1} charge window. Overlap slots: {overlap}"
            
            seen_windows[window_key] = i

    def test_no_discharge_overlap(self, optimizer, date_str):
        """Verify that discharge windows do not overlap at all (zero overlap)."""
        prices_dict = load_prices_for_date(date_str)
        if prices_dict is None:
            pytest.skip(f"No price data available for {date_str}")
        
        plan = optimizer.analyze_day(prices_dict, current_soc=30.0)
        
        if len(plan.cycles) < 2:
            pytest.skip(f"Only {len(plan.cycles)} cycle(s) for {date_str} - cannot test overlap")
        
        # Check all pairs of discharge windows
        for i in range(len(plan.cycles)):
            for j in range(i + 1, len(plan.cycles)):
                d1 = plan.cycles[i].discharge_window
                d2 = plan.cycles[j].discharge_window
                
                overlap_start = max(d1.start_slot, d2.start_slot)
                overlap_end = min(d1.end_slot, d2.end_slot)
                overlap_slots = max(0, overlap_end - overlap_start)
                
                assert overlap_slots == 0, \
                    f"{date_str}: Discharge windows {i+1} and {j+1} overlap by {overlap_slots} slots. " \
                    f"D1: {d1.start_time}-{d1.end_time}, D2: {d2.start_time}-{d2.end_time}"

    def test_charge_before_discharge(self, optimizer, date_str):
        """Verify that all charge windows start before their corresponding discharge windows."""
        prices_dict = load_prices_for_date(date_str)
        if prices_dict is None:
            pytest.skip(f"No price data available for {date_str}")
        
        plan = optimizer.analyze_day(prices_dict, current_soc=30.0)
        
        if not plan.cycles:
            pytest.skip(f"No cycles found for {date_str}")
        
        for i, cycle in enumerate(plan.cycles):
            assert cycle.charge_window.start_slot < cycle.discharge_window.start_slot, \
                f"{date_str}: Cycle {i+1} charge starts at slot {cycle.charge_window.start_slot} " \
                f"({cycle.charge_window.start_time}) but discharge starts at slot {cycle.discharge_window.start_slot} " \
                f"({cycle.discharge_window.start_time})"

    def test_meaningful_spread(self, optimizer, date_str):
        """Verify that cycles have meaningful spread accounting for inefficiency losses.
        
        Allows 1% tolerance for rounding/real-world price noise.
        """
        prices_dict = load_prices_for_date(date_str)
        if prices_dict is None:
            pytest.skip(f"No price data available for {date_str}")
        
        plan = optimizer.analyze_day(prices_dict, current_soc=30.0)
        
        if not plan.cycles:
            pytest.skip(f"No cycles found for {date_str}")
        
        # Allow 1% tolerance for marginal cases
        tolerance = 0.99
        
        for i, cycle in enumerate(plan.cycles):
            # Discharge price must be at least charge_price * price_multiplier
            # to account for battery inefficiency losses
            min_discharge_price = cycle.charge_window.avg_price * optimizer.price_multiplier * tolerance
            actual_discharge_price = cycle.discharge_window.avg_price
            
            assert actual_discharge_price >= min_discharge_price, \
                f"{date_str}: Cycle {i+1} discharge price {actual_discharge_price:.2f} must be >= " \
                f"{min_discharge_price:.2f} (charge {cycle.charge_window.avg_price:.2f} * " \
                f"multiplier {optimizer.price_multiplier} * tolerance {tolerance}) to account for inefficiency"
            
            # Also verify spread is positive (redundant but explicit)
            assert cycle.spread > 0, \
                f"{date_str}: Cycle {i+1} has non-positive spread: {cycle.spread:.2f}"

    def test_discharge_duration_validation(self, optimizer, date_str):
        """Verify that discharge windows are long enough for the energy charged (non-overnight cycles)."""
        prices_dict = load_prices_for_date(date_str)
        if prices_dict is None:
            pytest.skip(f"No price data available for {date_str}")
        
        plan = optimizer.analyze_day(prices_dict, current_soc=30.0)
        
        if not plan.cycles:
            pytest.skip(f"No cycles found for {date_str}")
        
        for i, cycle in enumerate(plan.cycles):
            if not is_overnight(cycle.charge_window):
                # For non-overnight cycles, validate discharge duration
                is_valid = optimizer._validate_discharge_window(
                    cycle.charge_window,
                    cycle.discharge_window,
                    start_soc=30.0  # Use test SOC
                )
                assert is_valid, \
                    f"{date_str}: Cycle {i+1} (non-overnight) has discharge window too short " \
                    f"({cycle.discharge_window.duration_hours:.2f}h) for energy charged " \
                    f"({cycle.charge_window.duration_hours:.2f}h)"

    def test_single_charge_window_duration_reasonable(self, optimizer, date_str):
        """Verify that no single charge window exceeds the charge_to_full time.
        
        Multiple charge windows throughout the day are fine (multiple arbitrage opportunities),
        but a single window shouldn't be longer than needed to fully charge the battery.
        """
        prices_dict = load_prices_for_date(date_str)
        if prices_dict is None:
            pytest.skip(f"No price data available for {date_str}")
        
        plan = optimizer.analyze_day(prices_dict, current_soc=30.0)
        
        if not plan.cycles:
            pytest.skip(f"No cycles found for {date_str}")
        
        # Allow some buffer for slot alignment (15-min granularity)
        max_single_window = optimizer.charge_hours + 0.5  # charge_to_full + 30min buffer
        
        for i, cycle in enumerate(plan.cycles):
            assert cycle.charge_window.duration_hours <= max_single_window, \
                f"{date_str}: Cycle {i+1} charge window ({cycle.charge_window.duration_hours:.2f}h) " \
                f"exceeds max single window {max_single_window:.2f}h (charge_to_full={optimizer.charge_hours}h)"

    def test_cycles_have_positive_spread(self, optimizer, date_str):
        """Verify that all cycles have positive spread.
        
        Note: The detailed profitability check (discharge_price >= charge_price * price_multiplier)
        is done in test_meaningful_spread. This test just ensures spread is positive.
        """
        prices_dict = load_prices_for_date(date_str)
        if prices_dict is None:
            pytest.skip(f"No price data available for {date_str}")
        
        plan = optimizer.analyze_day(prices_dict, current_soc=30.0)
        
        if not plan.cycles:
            pytest.skip(f"No cycles found for {date_str}")
        
        for i, cycle in enumerate(plan.cycles):
            assert cycle.spread > 0, \
                f"{date_str}: Cycle {i+1} has non-positive spread: {cycle.spread:.2f}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
