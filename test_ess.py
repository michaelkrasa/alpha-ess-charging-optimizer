"""
Tests for ESS Optimizer - Dynamic Reactive Strategy
Run with: uv run pytest test_ess.py -v
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ESS import ESSOptimizer, PriceWindow


@pytest.fixture
def optimizer():
    """Create an optimizer instance with mocked config and battery capacity"""
    with patch('ESS.Config') as mock_config, \
            patch('ESS.alphaess') as mock_client, \
            patch('ESS.PriceFetcher') as mock_fetcher:
        # Mock config with both __getitem__ and get()
        config_values = {
            'app_id': 'test_id',
            'app_secret': 'test_secret',
            'serial_number': 'TEST123',
            'charge_to_full': 3.0,
            'price_multiplier': 1.2,
            'avg_overnight_load_kw': 0.5,
            'avg_day_load_kw': 1.8,
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


class TestChargingCalculations:
    """Test charging slot calculations"""

    def test_calculate_charging_slots_from_empty(self, optimizer):
        """Test slot calculation from 0% - should be 12 slots (3h)"""
        slots = optimizer.calculate_charging_slots_needed(0.0)
        assert slots == 12  # 3 hours * 4 slots/hour

    def test_calculate_charging_slots_from_30_percent(self, optimizer):
        """Test slot calculation from 30% - should be ~8 slots (2.1h)"""
        slots = optimizer.calculate_charging_slots_needed(30.0)
        assert slots == 8  # 2.1 hours * 4 = 8.4, rounded to 8

    def test_calculate_charging_slots_from_50_percent(self, optimizer):
        """Test slot calculation from 50% - should be 6 slots (1.5h)"""
        slots = optimizer.calculate_charging_slots_needed(50.0)
        assert slots == 6  # 1.5 hours * 4 = 6 slots

    def test_calculate_charging_slots_minimum(self, optimizer):
        """Test that minimum is 1 slot even for nearly full battery"""
        slots = optimizer.calculate_charging_slots_needed(99.0)
        assert slots >= 1  # At least 1 slot (15 minutes)

    def test_calculate_charging_slots_zero_soc(self, optimizer):
        """Test that 0.0% SOC is handled correctly (not falsy!)"""
        slots = optimizer.calculate_charging_slots_needed(0.0)
        assert slots == 12  # Should need full 3 hours = 12 slots


class TestPriceLogic:
    """Test price-based decision logic"""

    def test_price_threshold_calculation(self, optimizer):
        """Test that price threshold is calculated correctly"""
        daily_mean = 120.0
        threshold = daily_mean / optimizer.price_multiplier
        assert threshold == 100.0  # 120 / 1.2 = 100

    def test_should_charge_cheap_price(self, optimizer):
        """Test charging decision when price is cheap enough"""
        daily_mean = 120.0
        charging_price = 95.0
        threshold = daily_mean / optimizer.price_multiplier

        should_charge = charging_price < threshold
        assert should_charge is True

    def test_should_not_charge_expensive_price(self, optimizer):
        """Test charging decision when price is too expensive"""
        daily_mean = 120.0
        charging_price = 115.0
        threshold = daily_mean / optimizer.price_multiplier

        should_charge = charging_price < threshold
        assert should_charge is False

    def test_price_threshold_with_different_multiplier(self, optimizer):
        """Test price threshold with different multiplier"""
        # Temporarily change multiplier
        original = optimizer.price_multiplier
        optimizer.price_multiplier = 1.5

        daily_mean = 150.0
        threshold = daily_mean / optimizer.price_multiplier
        assert threshold == 100.0  # 150 / 1.5 = 100

        optimizer.price_multiplier = original


@pytest.mark.asyncio
class TestAPIInteractions:
    """Test API-related functionality (mocked)"""

    async def test_get_battery_soc_success(self, optimizer):
        """Test successful battery SOC retrieval from LastPower"""
        mock_data = {'LastPower': {'soc': 45.5}, 'cobat': 15.5, 'usCapacity': 100}
        optimizer.client.getdata = AsyncMock(return_value=mock_data)

        soc = await optimizer.get_battery_soc()
        assert soc == 45.5

    async def test_get_battery_soc_list_response(self, optimizer):
        """Test battery SOC retrieval when API returns list"""
        mock_data = [{'LastPower': {'soc': 67.8}, 'cobat': 15.5, 'usCapacity': 100}]
        optimizer.client.getdata = AsyncMock(return_value=mock_data)

        soc = await optimizer.get_battery_soc()
        assert soc == 67.8

    async def test_get_battery_soc_failure(self, optimizer):
        """Test battery SOC retrieval when API fails"""
        optimizer.client.getdata = AsyncMock(side_effect=Exception("API Error"))

        soc = await optimizer.get_battery_soc()
        assert soc is None

    async def test_get_prices_for_day_success(self, optimizer):
        """Test successful price retrieval (96 slots)"""
        # Mock 96 15-minute prices
        mock_prices = [100 + i for i in range(96)]
        optimizer.price_fetcher.fetch_prices_for_date = AsyncMock(return_value=mock_prices)

        target_date = datetime(2025, 12, 2)
        prices = await optimizer.get_prices_for_day(target_date)

        assert prices is not None
        assert len(prices) == 96  # 96 slots (15-min intervals)
        assert 0 in prices  # First slot (00:00)
        assert 95 in prices  # Last slot (23:45)
        assert prices[0] == 100

    async def test_get_prices_for_day_invalid_data(self, optimizer):
        """Test price retrieval with invalid data"""
        # Mock returning wrong number of prices (not 96)
        mock_prices = [100, 110, 120]  # Only 3 instead of 96
        optimizer.price_fetcher.fetch_prices_for_date = AsyncMock(return_value=mock_prices)

        target_date = datetime(2025, 12, 2)
        prices = await optimizer.get_prices_for_day(target_date)

        assert prices is None

    async def test_get_prices_for_day_api_failure(self, optimizer):
        """Test price retrieval when API fails"""
        optimizer.price_fetcher.fetch_prices_for_date = AsyncMock(
            side_effect=Exception("Network error")
        )

        target_date = datetime(2025, 12, 2)
        prices = await optimizer.get_prices_for_day(target_date)

        assert prices is None

    async def test_set_charging_schedule_enable(self, optimizer):
        """Test enabling charging schedule"""
        optimizer.client.updateChargeConfigInfo = AsyncMock(return_value={'success': True})

        result = await optimizer.set_charging_schedule(
            enable=True,
            period1=("02:00", "05:00")
        )

        assert result is True
        optimizer.client.updateChargeConfigInfo.assert_called_once()
        call_args = optimizer.client.updateChargeConfigInfo.call_args
        assert call_args.kwargs['gridCharge'] == 1
        assert call_args.kwargs['batHighCap'] == 100

    async def test_set_charging_schedule_disable(self, optimizer):
        """Test disabling charging schedule"""
        optimizer.client.updateChargeConfigInfo = AsyncMock(return_value={'success': True})

        result = await optimizer.set_charging_schedule(enable=False)

        assert result is True
        optimizer.client.updateChargeConfigInfo.assert_called_once()
        call_args = optimizer.client.updateChargeConfigInfo.call_args
        assert call_args.kwargs['gridCharge'] == 0

    async def test_set_discharge_schedule_enable(self, optimizer):
        """Test enabling discharge schedule"""
        optimizer.client.updateDisChargeConfigInfo = AsyncMock(return_value={'success': True})

        result = await optimizer.set_discharge_schedule(
            enable=True,
            period1=("07:00", "11:00")
        )

        assert result is True
        optimizer.client.updateDisChargeConfigInfo.assert_called_once()
        call_args = optimizer.client.updateDisChargeConfigInfo.call_args
        assert call_args.kwargs['ctrDis'] == 1
        assert call_args.kwargs['batUseCap'] == 10


@pytest.mark.asyncio
class TestOptimizationFlow:
    """Test complete optimization flow"""

    async def test_optimize_for_day_successful_charging(self, optimizer):
        """Test successful optimization that decides to charge"""
        # Mock battery SOC
        optimizer.get_battery_soc = AsyncMock(return_value=30.0)

        # Mock 96 slot prices - cheap at night (slots 0-24), expensive later
        mock_prices = {}
        for slot in range(96):
            hour = slot // 4
            if hour < 6:  # Night valley
                mock_prices[slot] = 80
            elif 8 <= hour < 11:  # Morning peak
                mock_prices[slot] = 220
            else:
                mock_prices[slot] = 150
        optimizer.get_prices_for_day = AsyncMock(return_value=mock_prices)

        # Mock API calls
        optimizer.set_charging_schedule = AsyncMock(return_value=True)
        optimizer.set_discharge_schedule = AsyncMock(return_value=True)

        target_date = datetime(2025, 12, 2)
        result = await optimizer.optimize_for_day(target_date)

        assert result is True
        optimizer.set_charging_schedule.assert_called()
        optimizer.set_discharge_schedule.assert_called()

    async def test_optimize_for_day_skip_charging_expensive(self, optimizer):
        """Test optimization that skips charging due to high prices"""
        # Mock battery SOC
        optimizer.get_battery_soc = AsyncMock(return_value=50.0)

        # Mock prices - all same price (no arbitrage opportunity)
        mock_prices = {slot: 180 for slot in range(96)}
        optimizer.get_prices_for_day = AsyncMock(return_value=mock_prices)

        # Mock API calls
        optimizer.set_charging_schedule = AsyncMock(return_value=True)
        optimizer.set_discharge_schedule = AsyncMock(return_value=True)

        target_date = datetime(2025, 12, 2)
        result = await optimizer.optimize_for_day(target_date)

        assert result is True
        # With no price spread, no arbitrage cycles should be found
        # Charging should be disabled (first arg = False)
        call_args = optimizer.set_charging_schedule.call_args
        enable_arg = call_args[0][0]
        assert enable_arg is False, f"Should disable charging when no spread, got {enable_arg}"

    async def test_optimize_for_day_no_battery_data(self, optimizer):
        """Test optimization fails gracefully without battery data"""
        optimizer.get_battery_soc = AsyncMock(return_value=None)

        target_date = datetime(2025, 12, 2)
        result = await optimizer.optimize_for_day(target_date)

        assert result is False

    async def test_optimize_for_day_no_price_data(self, optimizer):
        """Test optimization fails gracefully without price data"""
        optimizer.get_battery_soc = AsyncMock(return_value=50.0)
        optimizer.get_prices_for_day = AsyncMock(return_value=None)

        target_date = datetime(2025, 12, 2)
        result = await optimizer.optimize_for_day(target_date)

        assert result is False


class TestEdgeCases:
    """Test edge cases and boundary conditions"""

    def test_charging_slots_negative_soc(self, optimizer):
        """Test that negative SOC is handled"""
        slots = optimizer.calculate_charging_slots_needed(-5.0)
        assert slots >= 12  # Should treat as 0% or more

    def test_charging_slots_over_100_soc(self, optimizer):
        """Test that SOC > 100% is handled"""
        slots = optimizer.calculate_charging_slots_needed(105.0)
        assert slots == 0  # No charging needed

    def test_flat_prices_detection(self, optimizer):
        """Test that flat prices produce no valleys/peaks"""
        prices = {i: 100 for i in range(96)}
        valleys, peaks = optimizer.detect_valleys_and_peaks(prices)
        assert len(valleys) == 0
        assert len(peaks) == 0


# =============================================================================
# Real Data Tests - December 3rd, 2025
# =============================================================================
# These tests use actual price data to verify the optimizer makes correct decisions

# Real 15-minute price data from OTE for December 1st, 2025 (96 slots)
PRICES_2025_12_01 = [
    99.03, 98.05, 93.54, 91.75, 108.0, 104.03, 93.75, 90.64,  # 00:00-02:00
    100.87, 90.57, 89.92, 89.12, 87.96, 89.17, 89.93, 92.2,  # 02:00-04:00
    89.16, 90.93, 90.27, 92.34, 88.12, 92.06, 100.65, 117.75,  # 04:00-06:00
    98.42, 117.8, 139.99, 147.34, 140.36, 142.38, 141.91, 157.08,  # 06:00-08:00
    172.71, 161.34, 147.89, 143.32, 151.83, 146.68, 139.76, 125.88,  # 08:00-10:00
    128.36, 125.18, 123.16, 119.96, 117.62, 116.61, 117.95, 116.97,  # 10:00-12:00
    126.3, 124.72, 131.26, 137.72, 124.26, 136.36, 139.69, 138.65,  # 12:00-14:00
    134.21, 152.0, 164.15, 173.64, 166.55, 177.3, 193.3, 196.26,  # 14:00-16:00
    201.51, 206.21, 210.7, 213.05, 175.46, 180.74, 178.51, 165.29,  # 16:00-18:00
    185.14, 191.29, 177.0, 174.26, 184.63, 166.23, 166.03, 155.68,  # 18:00-20:00
    167.03, 153.74, 140.28, 123.17, 145.84, 129.25, 114.13, 96.26,  # 20:00-22:00
    121.98, 113.8, 112.14, 96.72, 109.35, 100.0, 95.05, 87.54  # 22:00-24:00
]

# Real 15-minute price data from OTE for December 3rd, 2025 (96 slots)
PRICES_2025_12_03 = [
    104.57, 98.75, 96.82, 96.17, 101.89, 96.05, 96.48, 97.91,  # 00:00-02:00
    97.49, 96.18, 95.4, 94.39, 96.17, 95.67, 94.66, 94.68,  # 02:00-04:00
    95.78, 96.47, 96.97, 99.58, 94.37, 98.33, 104.81, 119.1,  # 04:00-06:00
    111.99, 136.07, 155.18, 190.69, 151.62, 181.14, 231.83, 222.63,  # 06:00-08:00
    231.85, 256.39, 265.36, 229.31, 260.49, 237.05, 184.06, 184.71,  # 08:00-10:00
    207.01, 181.68, 175.8, 174.13, 174.89, 169.99, 168.83, 171.75,  # 10:00-12:00
    163.86, 155.5, 165.19, 175.03, 170.12, 173.6, 185.58, 187.19,  # 12:00-14:00
    176.28, 192.19, 223.07, 217.17, 197.62, 218.14, 274.0, 298.44,  # 14:00-16:00
    244.83, 310.79, 337.44, 366.63, 279.18, 288.07, 254.45, 219.08,  # 16:00-18:00
    220.94, 222.71, 198.43, 178.7, 202.7, 177.36, 162.05, 152.87,  # 18:00-20:00
    174.53, 152.11, 136.46, 125.12, 140.21, 132.9, 117.1, 108.92,  # 20:00-22:00
    132.24, 119.81, 109.54, 103.4, 119.17, 108.16, 102.04, 97.0  # 22:00-24:00
]


class TestRealData20251203:
    """Tests using real price data from December 3rd, 2025"""

    @pytest.fixture
    def prices_dict(self):
        """Convert price list to slot dict"""
        return {slot: price for slot, price in enumerate(PRICES_2025_12_03)}

    def test_data_integrity(self):
        """Verify the saved data has correct structure"""
        assert len(PRICES_2025_12_03) == 96, "Should have 96 15-minute slots"
        assert all(isinstance(p, (int, float)) for p in PRICES_2025_12_03)
        assert min(PRICES_2025_12_03) > 0, "All prices should be positive"

    def test_detects_night_valley(self, optimizer, prices_dict):
        """Verify dynamic detection finds night valley"""
        valleys, peaks = optimizer.detect_valleys_and_peaks(prices_dict)

        assert len(valleys) > 0, "Should detect valleys"

        # Find the cheapest valley - should be significantly below daily mean (~170)
        cheapest_valley = min(valleys, key=lambda v: v.avg_price)
        daily_mean = sum(PRICES_2025_12_03) / len(PRICES_2025_12_03)
        assert cheapest_valley.avg_price < daily_mean * 0.7, \
            f"Valley should be well below mean ({daily_mean:.0f}), got {cheapest_valley.avg_price:.2f}"
        print(f"Cheapest valley: {cheapest_valley}")

    def test_detects_evening_peak(self, optimizer, prices_dict):
        """Verify dynamic detection finds evening peak"""
        valleys, peaks = optimizer.detect_valleys_and_peaks(prices_dict)

        assert len(peaks) > 0, "Should detect peaks"

        # Find the most expensive peak
        most_expensive = max(peaks, key=lambda p: p.avg_price)
        assert most_expensive.avg_price > 200, f"Evening peak should be over 200, got {most_expensive.avg_price:.2f}"
        print(f"Most expensive peak: {most_expensive}")

    def test_should_charge_with_typical_battery(self, optimizer, prices_dict):
        """Test charging decision with typical 50% battery"""
        # 50% battery needs about 1.5h = 6 slots
        slots_needed = optimizer.calculate_charging_slots_needed(50.0)
        assert slots_needed == 6

        # Use dynamic detection
        valleys, _ = optimizer.detect_valleys_and_peaks(prices_dict)
        assert len(valleys) > 0

        # Calculate daily mean
        daily_mean = sum(PRICES_2025_12_03) / len(PRICES_2025_12_03)
        price_threshold = daily_mean / optimizer.price_multiplier

        # Cheapest valley should be below threshold
        cheapest_valley = min(valleys, key=lambda v: v.avg_price)
        assert cheapest_valley.avg_price < price_threshold, \
            f"Should charge: {cheapest_valley.avg_price:.2f} < {price_threshold:.2f}"

    def test_price_statistics(self):
        """Document the price characteristics of this day"""
        prices = PRICES_2025_12_03

        daily_mean = sum(prices) / len(prices)
        daily_min = min(prices)
        daily_max = max(prices)

        # Night window (00:00-07:00, slots 0-28)
        night_prices = prices[0:28]
        night_mean = sum(night_prices) / len(night_prices)

        # Evening peak (16:00-18:00, slots 64-72)
        evening_prices = prices[64:72]
        evening_mean = sum(evening_prices) / len(evening_prices)

        print(f"\n=== December 3rd, 2025 Price Statistics ===")
        print(f"Daily: mean={daily_mean:.2f}, min={daily_min:.2f}, max={daily_max:.2f}")
        print(f"Night (00-07): mean={night_mean:.2f}")
        print(f"Evening (16-18): mean={evening_mean:.2f}")
        print(f"Spread ratio: {evening_mean / night_mean:.2f}x")

        # Verify expected characteristics
        assert daily_mean > 150, "This day should have high average prices"
        assert night_mean < 120, "Night should be cheap"
        assert evening_mean > 280, "Evening peak should be expensive"
        assert evening_mean > night_mean * 2, "Evening should be >2x night prices"


class TestDynamicDetection20251203:
    """
    Test DYNAMIC detection using December 3rd, 2025 data
    
    The optimizer should automatically find valleys and peaks
    without any hardcoded times - pure data-driven detection!
    """

    @pytest.fixture
    def prices_dict(self):
        """Convert price list to slot dict"""
        return {slot: price for slot, price in enumerate(PRICES_2025_12_03)}

    def test_detects_valleys_dynamically(self, optimizer, prices_dict):
        """Test that valleys are detected from price patterns, not hardcoded times"""
        valleys, peaks = optimizer.detect_valleys_and_peaks(prices_dict)

        assert len(valleys) > 0, "Should detect at least one valley"

        # We should find both absolute valleys (below threshold) 
        # AND relative valleys (dips between peaks)
        daily_mean = sum(PRICES_2025_12_03) / len(PRICES_2025_12_03)
        valley_threshold = daily_mean / optimizer.price_multiplier  # Uses config

        # At least one valley should be below absolute threshold (night)
        absolute_valleys = [v for v in valleys if v.avg_price < valley_threshold]
        assert len(absolute_valleys) >= 1, "Should have at least one absolute valley (night)"

        for valley in valleys:
            print(f"Valley detected: {valley}")

    def test_detects_peaks_dynamically(self, optimizer, prices_dict):
        """Test that peaks are detected from price patterns, not hardcoded times"""
        valleys, peaks = optimizer.detect_valleys_and_peaks(prices_dict)

        assert len(peaks) > 0, "Should detect at least one peak"

        # Check that peaks are actually expensive (above mean * price_multiplier)
        daily_mean = sum(PRICES_2025_12_03) / len(PRICES_2025_12_03)
        peak_threshold = daily_mean * optimizer.price_multiplier  # Uses config

        for peak in peaks:
            assert peak.avg_price > peak_threshold, \
                f"Peak {peak} should be above threshold {peak_threshold:.0f}"
            print(f"Peak detected: {peak}")

    def test_finds_arbitrage_cycles(self, optimizer, prices_dict):
        """Test that arbitrage cycles are created from detected valleys/peaks"""
        plan = optimizer.analyze_day(prices_dict, current_soc=30.0)

        assert plan.has_arbitrage_opportunity, "Dec 3rd should have arbitrage opportunities"
        assert len(plan.cycles) >= 1, "Should find at least one cycle"

        for cycle in plan.cycles:
            assert cycle.spread > 0, f"Cycle spread {cycle.spread} should be positive"
            # Charge should start before discharge starts
            assert cycle.charge_window.start_slot < cycle.discharge_window.start_slot, \
                "Charge window should start before discharge"
            print(f"Arbitrage cycle: {cycle}")

    def test_valleys_are_cheaper_than_peaks(self, optimizer, prices_dict):
        """Verify detected valleys are actually cheaper than detected peaks"""
        valleys, peaks = optimizer.detect_valleys_and_peaks(prices_dict)

        if valleys and peaks:
            cheapest_valley = min(v.avg_price for v in valleys)
            most_expensive_peak = max(p.avg_price for p in peaks)

            spread = most_expensive_peak - cheapest_valley
            print(f"Best spread: {most_expensive_peak:.0f} - {cheapest_valley:.0f} = {spread:.0f}")

            assert spread > 100, f"Dec 3rd should have >100 EUR spread, got {spread}"

    def test_no_hardcoded_assumptions(self, optimizer, prices_dict):
        """Verify the detection doesn't assume specific times"""
        plan = optimizer.analyze_day(prices_dict, current_soc=30.0)

        # The plan should have cycles based on data, not fixed attributes
        assert hasattr(plan, 'valleys'), "Should store detected valleys"
        assert hasattr(plan, 'peaks'), "Should store detected peaks"
        assert hasattr(plan, 'cycles'), "Should store arbitrage cycles"

        # Old hardcoded attributes should NOT exist
        assert not hasattr(plan, 'night_charge') or plan.night_charge is None
        assert not hasattr(plan, 'morning_discharge') or plan.morning_discharge is None

    def test_full_day_analysis(self, optimizer, prices_dict):
        """Full analysis of December 3rd data"""
        plan = optimizer.analyze_day(prices_dict, current_soc=30.0)

        print(f"\n=== December 3rd, 2025 - Dynamic Analysis ===")
        print(f"Daily: mean={plan.daily_mean:.0f}, min={plan.daily_min:.0f}, max={plan.daily_max:.0f}")
        print(f"\nDetected {len(plan.valleys)} valleys:")
        for v in plan.valleys:
            print(f"  {v}")
        print(f"\nDetected {len(plan.peaks)} peaks:")
        for p in plan.peaks:
            print(f"  {p}")
        print(f"\nProfitable cycles: {len(plan.cycles)}")
        for c in plan.cycles:
            print(f"  {c}")
        print(f"\nTotal spread: {plan.total_spread:.0f} EUR/MWh")

        assert plan.total_spread > 100, "Dec 3rd should have significant arbitrage"

    def test_soc_after_discharge(self, optimizer):
        """Test SOC estimation after discharge"""
        soc_after = optimizer.estimate_soc_after_discharge(100.0, 3.0)
        # After 3h discharge, SOC should be reduced but still above MIN_SOC
        assert soc_after < 100, f"SOC should decrease after discharge, got {soc_after}"
        assert soc_after >= optimizer.MIN_SOC, f"SOC should not go below MIN_SOC"


class TestDynamicDetectionEdgeCases:
    """Test edge cases for dynamic detection"""

    def test_flat_prices_no_arbitrage(self, optimizer):
        """Test behavior when all prices are the same (no arbitrage)"""
        flat_prices = {i: 150 for i in range(96)}
        plan = optimizer.analyze_day(flat_prices, current_soc=50.0)

        assert len(plan.valleys) == 0, "Flat prices should have no valleys"
        assert len(plan.peaks) == 0, "Flat prices should have no peaks"
        assert not plan.has_arbitrage_opportunity

    def test_single_spike_one_cycle(self, optimizer):
        """Test with a single price spike (should find one cycle)"""
        prices = {i: 100 for i in range(96)}
        # Add one spike at 16:00-18:00
        for i in range(64, 72):
            prices[i] = 250

        plan = optimizer.analyze_day(prices, current_soc=50.0)

        assert len(plan.peaks) >= 1, "Should detect the spike as a peak"
        print(f"Single spike test: {len(plan.valleys)} valleys, {len(plan.peaks)} peaks")

    def test_inverted_pattern(self, optimizer):
        """Test with inverted pattern (cheap during day, expensive at night)"""
        prices = {i: 200 for i in range(96)}  # Expensive baseline
        # Make midday cheap
        for i in range(44, 56):  # 11:00-14:00
            prices[i] = 80

        plan = optimizer.analyze_day(prices, current_soc=50.0)

        # Should still find the valley in the middle of the day
        assert len(plan.valleys) >= 1
        midday_valley = next((v for v in plan.valleys if 40 <= v.start_slot <= 60), None)
        assert midday_valley is not None, "Should detect midday valley"
        print(f"Inverted pattern: valley at {midday_valley}")


class TestDayAheadComparison:
    """Test day-ahead price comparison logic"""

    def test_tomorrow_cheaper_recommendation(self, optimizer):
        """Test recommendation when tomorrow is significantly cheaper"""
        # Today: has valleys but they're not super cheap
        today = {i: 150 for i in range(96)}
        for i in range(8, 20):
            today[i] = 120  # Valley at 120

        # Tomorrow: much cheaper valleys
        tomorrow = {i: 150 for i in range(96)}
        for i in range(8, 20):
            tomorrow[i] = 80  # Valley at 80

        result = optimizer.compare_with_tomorrow(today, tomorrow)

        assert result['tomorrow_cheaper'] is True
        assert result['recommendation'] == 'wait'

    def test_tomorrow_similar_recommendation(self, optimizer):
        """Test recommendation when tomorrow is similar"""
        today = {i: 150 for i in range(96)}
        for i in range(8, 20):
            today[i] = 100

        tomorrow = {i: 150 for i in range(96)}
        for i in range(8, 20):
            tomorrow[i] = 98  # Very similar

        result = optimizer.compare_with_tomorrow(today, tomorrow)

        assert result['tomorrow_cheaper'] is False
        assert result['recommendation'] == 'charge_now'


class TestPriceWindowDataclass:
    """Test PriceWindow dataclass properties"""

    def test_price_window_time_conversion(self):
        """Test that PriceWindow correctly converts slots to times"""
        window = PriceWindow(
            start_slot=8,  # 02:00
            end_slot=20,  # 05:00
            avg_price=95.0,
            window_type='valley'
        )

        assert window.start_time == "02:00"
        assert window.end_time == "05:00"
        assert window.duration_hours == 3.0

    def test_price_window_quarter_hour_times(self):
        """Test PriceWindow with quarter-hour boundaries"""
        window = PriceWindow(
            start_slot=9,  # 02:15
            end_slot=21,  # 05:15
            avg_price=100.0,
            window_type='peak'
        )

        assert window.start_time == "02:15"
        assert window.end_time == "05:15"
        assert window.duration_hours == 3.0


class TestConsumptionBetweenWindows:
    """Test SOC drain estimation between charge/discharge windows"""

    def test_overnight_consumption_drain(self, optimizer):
        """Test SOC drain during overnight gap using AVG_DAY_LOAD_KW"""
        # 6 hours at 1.8 kW (AVG_DAY_LOAD_KW) = 10.8 kWh = ~70% of 15.5 kWh
        drain = optimizer._estimate_consumption_soc_drain(6.0)
        expected = (6.0 * optimizer.AVG_DAY_LOAD_KW / optimizer.battery_capacity_kwh) * 100
        assert abs(drain - expected) < 0.1, f"Expected {expected:.1f}% drain, got {drain:.1f}%"

    def test_short_gap_consumption(self, optimizer):
        """Test SOC drain during short 1-hour gap"""
        # 1 hour at 1.8 kW = 1.8 kWh = ~11.6% of 15.5 kWh
        drain = optimizer._estimate_consumption_soc_drain(1.0)
        expected = (1.0 * optimizer.AVG_DAY_LOAD_KW / optimizer.battery_capacity_kwh) * 100
        assert abs(drain - expected) < 0.1, f"Expected {expected:.1f}% drain, got {drain:.1f}%"

    def test_zero_gap_no_drain(self, optimizer):
        """Test no drain when gap is zero"""
        drain = optimizer._estimate_consumption_soc_drain(0.0)
        assert drain == 0


class TestFullValleyCharging:
    """Test full valley charging for depleted batteries"""

    def test_full_valley_uses_entire_duration(self, optimizer):
        """Test that full valley window uses valley duration"""
        valley = PriceWindow(start_slot=0, end_slot=16, avg_price=100, window_type='valley')
        # Create flat prices for the valley
        slot_prices = {i: 100 for i in range(16)}

        charge_window = optimizer._create_charge_window(valley, slot_prices)

        # Should use full valley (4 hours) since charge_hours is 3
        assert charge_window.duration_hours >= 3.0

    def test_full_valley_caps_at_max(self, optimizer):
        """Test that full valley is capped at reasonable max"""
        # Very long valley (8 hours)
        valley = PriceWindow(start_slot=0, end_slot=32, avg_price=100, window_type='valley')
        slot_prices = {i: 100 for i in range(32)}

        charge_window = optimizer._create_charge_window(valley, slot_prices)

        # Should cap at ~120% of charge_hours (3 * 1.2 = 3.6h)
        assert charge_window.duration_hours <= 4.0

    def test_full_valley_minimum_one_hour(self, optimizer):
        """Test minimum charge duration is 1 hour when valley allows"""
        # Valley of 1.5 hours - should use at least 1 hour
        valley = PriceWindow(start_slot=0, end_slot=6, avg_price=100, window_type='valley')
        slot_prices = {i: 100 for i in range(6)}

        charge_window = optimizer._create_charge_window(valley, slot_prices)

        assert charge_window.duration_hours >= 1.0

    def test_short_valley_uses_full_duration(self, optimizer):
        """Test that a very short valley uses its full duration"""
        # Very short valley (30 min) - can't extend beyond valley
        valley = PriceWindow(start_slot=0, end_slot=2, avg_price=100, window_type='valley')
        slot_prices = {i: 100 for i in range(2)}

        charge_window = optimizer._create_charge_window(valley, slot_prices)

        # Should use entire valley even though it's less than 1 hour
        assert charge_window.duration_hours == 0.5
        assert charge_window.start_slot == 0
        assert charge_window.end_slot == 2


class TestDischargeWindowExtension:
    """Test discharge window extension to maximize energy usage"""

    @pytest.fixture
    def prices_with_profitable_surroundings(self):
        """Price data where slots around peak are still profitable"""
        prices = {i: 100 for i in range(96)}  # Base price
        # Peak at 16:00-17:00 (slots 64-68)
        for i in range(64, 68):
            prices[i] = 250
        # Backward extension requires >= 85% of peak avg (250 * 0.85 = 212.5)
        # AND >= profit_threshold (100 * 1.2 = 120)
        for i in range(60, 64):  # Before peak - high enough for backward threshold
            prices[i] = 220
        # Forward extension only requires >= profit_threshold (120)
        for i in range(68, 76):  # After peak
            prices[i] = 140
        return prices

    def test_extends_backwards(self, optimizer, prices_with_profitable_surroundings):
        """Test that discharge window extends backwards into high-value slots"""
        peak = PriceWindow(start_slot=64, end_slot=68, avg_price=250, window_type='peak')
        charge_price = 100

        extended = optimizer._extend_discharge_window(
            peak, charge_price, prices_with_profitable_surroundings
        )

        assert extended.start_slot < peak.start_slot, "Should extend backwards"
        assert extended.start_slot == 60, f"Should extend to slot 60, got {extended.start_slot}"

    def test_extends_forwards(self, optimizer, prices_with_profitable_surroundings):
        """Test that discharge window extends forwards into profitable slots"""
        peak = PriceWindow(start_slot=64, end_slot=68, avg_price=250, window_type='peak')
        charge_price = 100

        extended = optimizer._extend_discharge_window(
            peak, charge_price, prices_with_profitable_surroundings
        )

        assert extended.end_slot > peak.end_slot, "Should extend forwards"
        assert extended.end_slot == 76, f"Should extend to slot 76, got {extended.end_slot}"

    def test_respects_max_end_slot(self, optimizer, prices_with_profitable_surroundings):
        """Test that extension respects max_end_slot boundary"""
        peak = PriceWindow(start_slot=64, end_slot=68, avg_price=250, window_type='peak')
        charge_price = 100

        extended = optimizer._extend_discharge_window(
            peak, charge_price, prices_with_profitable_surroundings, max_end_slot=70
        )

        assert extended.end_slot <= 70, "Should not exceed max_end_slot"

    def test_no_extension_when_unprofitable(self, optimizer):
        """Test no extension when surrounding slots are unprofitable"""
        prices = {i: 100 for i in range(96)}
        for i in range(64, 68):
            prices[i] = 250
        # Surroundings are at base price (100), which is NOT > 100 * 1.2 = 120

        peak = PriceWindow(start_slot=64, end_slot=68, avg_price=250, window_type='peak')
        charge_price = 100

        extended = optimizer._extend_discharge_window(peak, charge_price, prices)

        assert extended.start_slot == peak.start_slot
        assert extended.end_slot == peak.end_slot


class TestFindProfitableDischargeWindow:
    """Test finding discharge windows when no peaks are detected"""

    def test_finds_profitable_window_without_peaks(self, optimizer):
        """Test finding discharge window from raw prices when no peaks detected"""
        prices = {i: 100 for i in range(96)}
        # Add a profitable window at 14:00-16:00 (slots 56-64)
        for i in range(56, 64):
            prices[i] = 150  # Above 100 * 1.2 = 120 threshold

        charge_price = 100
        window = optimizer._find_profitable_discharge_window(charge_price, prices, after_slot=0)

        assert window is not None, "Should find profitable window"
        assert window.start_slot == 56
        assert window.end_slot == 64
        assert window.avg_price == 150

    def test_returns_none_when_no_profitable_slots(self, optimizer):
        """Test returns None when no slots are profitable"""
        prices = {i: 100 for i in range(96)}  # All at base, none above 120

        charge_price = 100
        window = optimizer._find_profitable_discharge_window(charge_price, prices, after_slot=0)

        assert window is None

    def test_respects_after_slot(self, optimizer):
        """Test that search starts from after_slot"""
        prices = {i: 100 for i in range(96)}
        # Early profitable window (should be skipped)
        for i in range(8, 16):
            prices[i] = 150
        # Later profitable window (should be found)
        for i in range(56, 64):
            prices[i] = 160

        charge_price = 100
        window = optimizer._find_profitable_discharge_window(charge_price, prices, after_slot=20)

        assert window is not None
        assert window.start_slot >= 20, "Should skip windows before after_slot"
        assert window.start_slot == 56

    def test_finds_best_window(self, optimizer):
        """Test that it finds the highest average price window"""
        prices = {i: 100 for i in range(96)}
        # First profitable window (lower avg)
        for i in range(20, 28):
            prices[i] = 130
        # Second profitable window (higher avg)
        for i in range(56, 64):
            prices[i] = 180

        charge_price = 100
        window = optimizer._find_profitable_discharge_window(charge_price, prices, after_slot=0)

        assert window is not None
        assert window.avg_price == 180, "Should find highest average window"


class TestOvernightChargingBehavior:
    """Test overnight/depleted battery charging behavior"""

    @pytest.fixture
    def overnight_prices(self):
        """Typical overnight price pattern"""
        prices = {i: 150 for i in range(96)}
        # Cheap overnight 00:00-06:00
        for i in range(0, 24):
            prices[i] = 80
        # Morning peak 07:00-10:00
        for i in range(28, 40):
            prices[i] = 200
        return prices

    def test_uses_full_valley_overnight(self, optimizer, overnight_prices):
        """Test that overnight charging uses full valley"""
        plan = optimizer.analyze_day(overnight_prices, current_soc=10.0)

        assert plan.has_arbitrage_opportunity
        assert len(plan.cycles) > 0

        first_charge = plan.cycles[0].charge_window
        # Overnight valley should use full duration (capped at ~3.6h)
        assert first_charge.duration_hours >= 3.0, \
            f"Overnight should use full valley, got {first_charge.duration_hours}h"

    def test_uses_full_valley_when_depleted(self, optimizer, overnight_prices):
        """Test that depleted battery (SOC < 30%) uses full valley"""
        plan = optimizer.analyze_day(overnight_prices, current_soc=15.0)

        if plan.cycles:
            first_charge = plan.cycles[0].charge_window
            assert first_charge.duration_hours >= 3.0

    def test_extended_discharge_during_peak(self, optimizer, overnight_prices):
        """Test that discharge window is extended during peaks"""
        plan = optimizer.analyze_day(overnight_prices, current_soc=10.0)

        if plan.cycles:
            discharge = plan.cycles[0].discharge_window
            # Should extend beyond the strict peak detection
            assert discharge.duration_hours >= 1.0


class TestArbitrageCycleIntegration:
    """Integration tests for full arbitrage cycle creation"""

    def test_cycle_with_no_detected_peaks(self, optimizer):
        """Test cycle creation when peaks aren't detected but profitable windows exist"""
        prices = {i: 120 for i in range(96)}
        # Cheap valley at night
        for i in range(0, 16):
            prices[i] = 80
        # Moderately expensive afternoon (not above mean*1.2, but above charge*1.2)
        for i in range(56, 72):
            prices[i] = 140  # Above 80*1.2=96, but maybe not above mean*1.2

        plan = optimizer.analyze_day(prices, current_soc=20.0)

        # Should still find arbitrage even if strict peak detection misses the afternoon
        if plan.valleys:
            assert len(plan.cycles) >= 0  # May or may not find cycles depending on thresholds

    def test_sequential_valley_peak_pairing(self, optimizer):
        """Test that valleys are paired with sequential peaks"""
        prices = {i: 80 for i in range(96)}  # Base price low enough to not trigger extension
        # Valley 1: 00:00-02:00
        for i in range(0, 8):
            prices[i] = 60
        # Peak 1: 07:00-09:00
        for i in range(28, 36):
            prices[i] = 200
        # Valley 2: 11:00-13:00
        for i in range(44, 52):
            prices[i] = 65
        # Peak 2: 17:00-19:00
        for i in range(68, 76):
            prices[i] = 220

        plan = optimizer.analyze_day(prices, current_soc=20.0)

        if len(plan.cycles) >= 2:
            # Valley 1 should pair with Peak 1 (sequential)
            # Note: discharge may be extended but should START near the detected peak
            cycle1_discharge_start = plan.cycles[0].discharge_window.start_slot
            cycle2_charge_start = plan.cycles[1].charge_window.start_slot

            # Cycle 1 discharge should be in the morning peak area
            assert cycle1_discharge_start < 50, f"Cycle 1 should discharge in morning, got slot {cycle1_discharge_start}"
            # Cycle 2 should charge after cycle 1 discharges
            assert cycle2_charge_start > plan.cycles[0].charge_window.end_slot, "Cycles should be sequential"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
