"""
Comprehensive tests for January 2026 real price data.

Tests the optimizer against actual price data for all days in January 2026,
validating that all rules are followed: no overlaps, proper spread, etc.

Note: Jan 31st 2026 had a bug where the charging window was only 30 minutes
in the evening, which is insufficient for meaningful charging.
"""

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import SLOTS_PER_DAY
from src.optimizer import ESSOptimizer

# Directory containing price data
DATA_DIR = Path(__file__).parent / 'test_data' / 'january_2026'


def discover_price_files():
    """Discover all available price data files."""
    if not DATA_DIR.exists():
        return []
    
    files = sorted(DATA_DIR.glob('2026-01-*.json'))
    return [f.stem for f in files]  # Return date strings like '2026-01-01'


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
            'charge_rate_kw': 5.0,
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


def slot_to_time(slot: int) -> str:
    """Convert slot index to HH:MM time string."""
    hours = slot // 4
    minutes = (slot % 4) * 15
    return f"{hours:02d}:{minutes:02d}"


# Discover available test data files
AVAILABLE_DATES = discover_price_files()


@pytest.mark.skipif(
    len(AVAILABLE_DATES) == 0,
    reason="No price data files found. Run fetch_january_prices.py first."
)
@pytest.mark.parametrize("date_str", AVAILABLE_DATES)
class TestJanuary2026DailyValidation:
    """Comprehensive validation tests for each day in January 2026."""

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
        
        Allows 2% tolerance for rounding/real-world price noise.
        """
        prices_dict = load_prices_for_date(date_str)
        if prices_dict is None:
            pytest.skip(f"No price data available for {date_str}")
        
        plan = optimizer.analyze_day(prices_dict, current_soc=30.0)
        
        if not plan.cycles:
            pytest.skip(f"No cycles found for {date_str}")
        
        # Allow 2% tolerance for marginal cases (rounding to half-hour can shift windows slightly)
        tolerance = 0.98
        
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
        """Verify that no single charge window exceeds the full charge time.
        
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
        max_single_window = optimizer.charge_hours + 0.5  # charge_hours + 30min buffer
        
        for i, cycle in enumerate(plan.cycles):
            assert cycle.charge_window.duration_hours <= max_single_window, \
                f"{date_str}: Cycle {i+1} charge window ({cycle.charge_window.duration_hours:.2f}h) " \
                f"exceeds max single window {max_single_window:.2f}h (charge_hours={optimizer.charge_hours}h)"

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

    def test_charge_window_sufficient_for_full_charge(self, optimizer, date_str):
        """Verify that charge windows are long enough to actually charge the battery meaningfully.
        
        A 30-minute charge window for a 15 kWh battery at 5 kW only adds ~16% SOC,
        which is likely a bug if that's the only charge window for the day.
        
        For overnight charging, the window should be sufficient to charge from
        typical overnight SOC (e.g., 30%) to near full.
        """
        prices_dict = load_prices_for_date(date_str)
        if prices_dict is None:
            pytest.skip(f"No price data available for {date_str}")
        
        plan = optimizer.analyze_day(prices_dict, current_soc=30.0)
        
        if not plan.cycles:
            pytest.skip(f"No cycles found for {date_str}")
        
        # Get minimum reasonable charge duration
        # Daytime: minimum 1 hour (4 slots) for partial arbitrage
        # Overnight: full charge time (capacity / charge_rate)
        min_charge_slots = 4  # 1 hour minimum for daytime
        min_overnight_slots = optimizer.battery_manager.calculate_full_charge_slots()
        
        for i, cycle in enumerate(plan.cycles):
            window_slots = cycle.charge_window.end_slot - cycle.charge_window.start_slot
            is_night = is_overnight(cycle.charge_window)
            
            if is_night:
                assert window_slots >= min_overnight_slots, \
                    f"{date_str}: Cycle {i+1} overnight charge window is only {window_slots} slots " \
                    f"({cycle.charge_window.duration_hours:.2f}h, {cycle.charge_window.start_time}-{cycle.charge_window.end_time}). " \
                    f"Expected at least {min_overnight_slots} slots ({min_overnight_slots / 4:.1f}h) for overnight charging."
            else:
                assert window_slots >= min_charge_slots, \
                    f"{date_str}: Cycle {i+1} daytime charge window is only {window_slots} slots " \
                    f"({cycle.charge_window.duration_hours:.2f}h, {cycle.charge_window.start_time}-{cycle.charge_window.end_time}). " \
                    f"Expected at least {min_charge_slots} slots (1h) for meaningful charging."


class TestJanuary31stSpecific:
    """Specific tests for January 31st 2026 where a bug was observed.
    
    The bug: charging window was only 30 minutes in the evening, which doesn't
    make sense for meaningful battery charging.
    """

    @pytest.fixture
    def optimizer(self):
        """Create an optimizer instance with mocked config matching real config.yaml."""
        with patch('src.optimizer.Config') as mock_config, \
                patch('src.ess_client.alphaess') as mock_client, \
                patch('src.optimizer.PriceFetcher') as mock_fetcher:
            config_values = {
                'app_id': 'test_id',
                'app_secret': 'test_secret',
                'serial_number': 'TEST123',
                'charge_rate_kw': 5.0,
                'price_multiplier': 1.18,
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

            mock_client_instance = MagicMock()
            mock_client_instance.close = AsyncMock()
            mock_client.return_value = mock_client_instance

            mock_fetcher_instance = MagicMock()
            mock_fetcher.return_value = mock_fetcher_instance

            opt = ESSOptimizer()
            opt.battery_capacity_kwh = 15.5
            return opt

    def test_jan31_charge_windows_reasonable(self, optimizer):
        """Test that Jan 31st doesn't produce absurdly short charge windows.
        
        Jan 31st 2026 has a flat overnight price pattern with only a tiny valley (2 slots).
        The optimizer should reject cycles with insufficient charge windows.
        """
        prices_dict = load_prices_for_date('2026-01-31')
        if prices_dict is None:
            pytest.skip("No price data available for 2026-01-31")
        
        plan = optimizer.analyze_day(prices_dict, current_soc=30.0)
        
        # Debug output
        print(f"\n=== Jan 31st 2026 Analysis ===")
        print(f"Daily mean: {plan.daily_mean:.2f}, min: {plan.daily_min:.2f}, max: {plan.daily_max:.2f}")
        print(f"Valleys detected: {len(plan.valleys)}")
        for i, v in enumerate(plan.valleys):
            print(f"  Valley {i+1}: {v.start_time}-{v.end_time} (slots {v.start_slot}-{v.end_slot}), avg={v.avg_price:.2f}")
        print(f"Peaks detected: {len(plan.peaks)}")
        for i, p in enumerate(plan.peaks):
            print(f"  Peak {i+1}: {p.start_time}-{p.end_time} (slots {p.start_slot}-{p.end_slot}), avg={p.avg_price:.2f}")
        print(f"Cycles: {len(plan.cycles)}")
        
        if plan.cycles:
            for i, c in enumerate(plan.cycles):
                charge_slots = c.charge_window.end_slot - c.charge_window.start_slot
                discharge_slots = c.discharge_window.end_slot - c.discharge_window.start_slot
                print(f"  Cycle {i+1}:")
                print(f"    Charge: {c.charge_window.start_time}-{c.charge_window.end_time} ({charge_slots} slots, {c.charge_window.duration_hours:.2f}h)")
                print(f"    Discharge: {c.discharge_window.start_time}-{c.discharge_window.end_time} ({discharge_slots} slots, {c.discharge_window.duration_hours:.2f}h)")
                print(f"    Spread: {c.spread:.2f}")
            
            # If cycles were created, verify they have reasonable charge windows
            for i, cycle in enumerate(plan.cycles):
                window_slots = cycle.charge_window.end_slot - cycle.charge_window.start_slot
                is_night = is_overnight(cycle.charge_window)
                if is_night:
                    # Overnight requires full charge time
                    min_required = optimizer.battery_manager.calculate_full_charge_slots()
                else:
                    min_required = 4  # 1 hour minimum for daytime
                assert window_slots >= min_required, \
                    f"Cycle {i+1} has only {window_slots} slots ({cycle.charge_window.duration_hours:.2f}h) " \
                    f"for charging ({cycle.charge_window.start_time}-{cycle.charge_window.end_time}). " \
                    f"Expected at least {min_required} slots ({min_required / 4:.1f}h)!"
        else:
            # No cycles is acceptable for days with flat prices and no good valleys
            print("  No cycles created (correct behavior for flat price day with insufficient valleys)")
            
            # Verify there was indeed a small valley that was correctly rejected
            small_valleys = [v for v in plan.valleys if (v.end_slot - v.start_slot) < 4]
            if small_valleys:
                print(f"  Correctly rejected {len(small_valleys)} small valley(s):")
                for v in small_valleys:
                    print(f"    {v.start_time}-{v.end_time} ({v.end_slot - v.start_slot} slots)")

    def test_jan31_evening_cycle_detection(self, optimizer):
        """Investigate if there's an evening charging issue on Jan 31st."""
        prices_dict = load_prices_for_date('2026-01-31')
        if prices_dict is None:
            pytest.skip("No price data available for 2026-01-31")
        
        # Check evening prices (after 17:00 = slot 68)
        evening_slots = range(68, 96)  # 17:00 to 24:00
        evening_prices = {s: prices_dict[s] for s in evening_slots}
        
        mean_price = sum(prices_dict.values()) / len(prices_dict)
        evening_mean = sum(evening_prices.values()) / len(evening_prices)
        
        print(f"\n=== Jan 31st Evening Analysis ===")
        print(f"Daily mean: {mean_price:.2f}")
        print(f"Evening mean (17:00-24:00): {evening_mean:.2f}")
        print(f"Evening prices by hour:")
        for hour in range(17, 24):
            hour_slots = range(hour * 4, (hour + 1) * 4)
            hour_prices = [prices_dict[s] for s in hour_slots]
            hour_avg = sum(hour_prices) / len(hour_prices)
            print(f"  {hour:02d}:00 - {hour+1:02d}:00: avg={hour_avg:.2f}, prices={hour_prices}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
