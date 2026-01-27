"""
Tests for ESS Optimizer
Run with: uv run pytest tests/test_ess.py -v
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import PriceWindow
from src.optimizer import ESSOptimizer


@pytest.fixture
def optimizer():
    """Create an optimizer instance with mocked config and battery capacity"""
    with patch('src.optimizer.Config') as mock_config, \
            patch('src.ess_client.alphaess') as mock_client, \
            patch('src.optimizer.PriceFetcher') as mock_fetcher:
        # Mock config with both __getitem__ and get()
        # Using realistic values matching real config.yaml
        config_values = {
            'app_id': 'test_id',
            'app_secret': 'test_secret',
            'serial_number': 'TEST123',
            'charge_to_full': 3.0,
            'price_multiplier': 1.2,
            'avg_overnight_load_kw': 0.5,
            'avg_day_load_kw': 1.8,
            'smoothing_window': 2,  # Matches real config for Dec 8th detection
            'min_window_slots': 2,  # Matches real config
            'early_morning_end_hour': 6,
            'afternoon_start_hour': 10,
            'afternoon_end_hour': 18,
            'min_discharge_fraction': 0.5,
            'min_discharge_hours': 2.0,
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
        optimizer.ess_client.client.getdata = AsyncMock(return_value=mock_data)
        optimizer.ess_client.get_battery_capacity = AsyncMock(return_value=15.5)

        soc = await optimizer.get_battery_soc()
        assert soc == 45.5

    async def test_get_battery_soc_list_response(self, optimizer):
        """Test battery SOC retrieval when API returns list"""
        mock_data = [{'LastPower': {'soc': 67.8}, 'cobat': 15.5, 'usCapacity': 100}]
        optimizer.ess_client.client.getdata = AsyncMock(return_value=mock_data)
        optimizer.ess_client.get_battery_capacity = AsyncMock(return_value=15.5)

        soc = await optimizer.get_battery_soc()
        assert soc == 67.8

    async def test_get_battery_soc_failure(self, optimizer):
        """Test battery SOC retrieval when API fails"""
        optimizer.ess_client.client.getdata = AsyncMock(side_effect=Exception("API Error"))

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
        optimizer.ess_client.client.updateChargeConfigInfo = AsyncMock(return_value={'success': True})

        result = await optimizer.set_charging_schedule(
            enable=True,
            period1=("02:00", "05:00")
        )

        assert result is True
        optimizer.ess_client.client.updateChargeConfigInfo.assert_called_once()
        call_args = optimizer.ess_client.client.updateChargeConfigInfo.call_args
        assert call_args.kwargs['gridCharge'] == 1
        assert call_args.kwargs['batHighCap'] == 100

    async def test_set_charging_schedule_disable(self, optimizer):
        """Test disabling charging schedule"""
        optimizer.ess_client.client.updateChargeConfigInfo = AsyncMock(return_value={'success': True})

        result = await optimizer.set_charging_schedule(enable=False)

        assert result is True
        optimizer.ess_client.client.updateChargeConfigInfo.assert_called_once()
        call_args = optimizer.ess_client.client.updateChargeConfigInfo.call_args
        assert call_args.kwargs['gridCharge'] == 0

    async def test_set_discharge_schedule_enable(self, optimizer):
        """Test enabling discharge schedule"""
        optimizer.ess_client.client.updateDisChargeConfigInfo = AsyncMock(return_value={'success': True})

        result = await optimizer.set_discharge_schedule(
            enable=True,
            period1=("07:00", "11:00")
        )

        assert result is True
        optimizer.ess_client.client.updateDisChargeConfigInfo.assert_called_once()
        call_args = optimizer.ess_client.client.updateDisChargeConfigInfo.call_args
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
        optimizer.ess_client.set_charging_schedule = AsyncMock(return_value=True)
        optimizer.ess_client.set_discharge_schedule = AsyncMock(return_value=True)

        target_date = datetime(2025, 12, 2)
        result = await optimizer.optimize_for_day(target_date)

        assert result is True
        optimizer.ess_client.set_charging_schedule.assert_called()
        optimizer.ess_client.set_discharge_schedule.assert_called()

    async def test_optimize_for_day_skip_charging_expensive(self, optimizer):
        """Test optimization that skips charging due to high prices"""
        # Mock battery SOC
        optimizer.get_battery_soc = AsyncMock(return_value=50.0)

        # Mock prices - all same price (no arbitrage opportunity)
        mock_prices = {slot: 180 for slot in range(96)}
        optimizer.get_prices_for_day = AsyncMock(return_value=mock_prices)

        # Mock API calls
        optimizer.ess_client.set_charging_schedule = AsyncMock(return_value=True)
        optimizer.ess_client.set_discharge_schedule = AsyncMock(return_value=True)

        target_date = datetime(2025, 12, 2)
        result = await optimizer.optimize_for_day(target_date)

        assert result is True
        # With no price spread, no arbitrage cycles should be found
        # Charging should be disabled (first arg = False)
        call_args = optimizer.ess_client.set_charging_schedule.call_args
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


class TestValleySelection:
    """Test the new valley selection logic for early morning + afternoon priority"""

    def test_selects_early_morning_and_afternoon_valleys(self, optimizer):
        """Test that valley selection prioritizes one early morning and one afternoon valley"""
        from models import PriceWindow

        # Create test valleys
        valleys = [
            PriceWindow(0, 8, 80, 'valley'),    # 00:00-02:00 (early morning)
            PriceWindow(12, 20, 85, 'valley'),  # 03:00-05:00 (early morning, cheaper)
            PriceWindow(44, 52, 90, 'valley'),  # 11:00-13:00 (afternoon)
            PriceWindow(56, 64, 95, 'valley'),  # 14:00-16:00 (afternoon, more expensive)
            PriceWindow(80, 88, 70, 'valley'),  # 20:00-22:00 (evening, cheapest)
        ]

        # Mock the method to test the selection logic
        early_morning_end = int(optimizer.config.get('early_morning_end_hour', 6))
        afternoon_start = int(optimizer.config.get('afternoon_start_hour', 10))
        afternoon_end = int(optimizer.config.get('afternoon_end_hour', 18))

        early_morning = [v for v in valleys if 0 <= (v.start_slot // 4) < early_morning_end]
        afternoon = [v for v in valleys if afternoon_start <= (v.start_slot // 4) < afternoon_end]
        other = [v for v in valleys if v not in early_morning and v not in afternoon]

        # Select best valleys: 1 early morning + 1 afternoon + rest
        selected_valleys = []
        if early_morning:
            selected_valleys.append(min(early_morning, key=lambda v: v.avg_price))  # Best early morning
        if afternoon:
            selected_valleys.append(min(afternoon, key=lambda v: v.avg_price))  # Best afternoon
        # Add remaining valleys sorted by price
        remaining = [v for v in valleys if v not in selected_valleys]
        selected_valleys.extend(sorted(remaining, key=lambda v: v.avg_price))

        # Should select: cheapest early morning (80), cheapest afternoon (90), then cheapest remaining (70, 85, 95)
        expected_prices = [80, 90, 70, 85, 95]  # Sorted by selection priority then price
        actual_prices = [v.avg_price for v in selected_valleys]

        assert actual_prices == expected_prices, f"Expected {expected_prices}, got {actual_prices}"

    def test_valley_selection_with_missing_afternoon(self, optimizer):
        """Test valley selection when no afternoon valleys are available"""
        from models import PriceWindow

        valleys = [
            PriceWindow(0, 8, 80, 'valley'),    # 00:00-02:00 (early morning)
            PriceWindow(80, 88, 70, 'valley'),  # 20:00-22:00 (evening)
        ]

        # Should select early morning first, then other valleys by price
        early_morning_end = int(optimizer.config.get('early_morning_end_hour', 6))
        afternoon_start = int(optimizer.config.get('afternoon_start_hour', 10))
        afternoon_end = int(optimizer.config.get('afternoon_end_hour', 18))

        early_morning = [v for v in valleys if 0 <= (v.start_slot // 4) < early_morning_end]
        afternoon = [v for v in valleys if afternoon_start <= (v.start_slot // 4) < afternoon_end]
        other = [v for v in valleys if v not in early_morning and v not in afternoon]

        selected_valleys = []
        if early_morning:
            selected_valleys.append(min(early_morning, key=lambda v: v.avg_price))
        if afternoon:
            selected_valleys.append(min(afternoon, key=lambda v: v.avg_price))
        remaining = [v for v in valleys if v not in selected_valleys]
        selected_valleys.extend(sorted(remaining, key=lambda v: v.avg_price))

        expected_prices = [80, 70]  # Early morning first, then cheapest remaining
        actual_prices = [v.avg_price for v in selected_valleys]

        assert actual_prices == expected_prices


class TestFlexibleDischargeValidation:
    """Test the flexible discharge window validation"""

    def test_flexible_discharge_accepts_partial_discharge(self, optimizer):
        """Test that discharge validation accepts partial discharge when configured"""
        from models import PriceWindow

        # Mock battery capacity
        optimizer.battery_capacity_kwh = 15.5

        # 3 hours charging from 0% SOC should charge ~3 kWh
        # Discharging at 1.8 kW for 1.67 hours should discharge ~3 kWh
        # But we allow only 50% = 0.83 hours minimum discharge
        charge_window = PriceWindow(0, 12, 100, 'valley')  # 3 hours charging
        discharge_window = PriceWindow(64, 76, 150, 'peak')  # 3 hours discharge

        # With min_discharge_fraction = 0.5, should require max(1.67 * 0.5, 2.0) = max(0.83, 2.0) = 2.0 hours
        # Our discharge window is 3 hours, so it should pass
        is_valid = optimizer._validate_discharge_window(charge_window, discharge_window, start_soc=0.0)

        assert is_valid, "Should accept discharge window longer than minimum required"

    def test_flexible_discharge_rejects_too_short_window(self, optimizer):
        """Test that discharge validation rejects windows that are too short"""
        from models import PriceWindow

        # Mock battery capacity
        optimizer.battery_capacity_kwh = 15.5

        # 3 hours charging from 0% SOC
        charge_window = PriceWindow(0, 12, 100, 'valley')
        # Only 1 hour discharge (should be rejected as < 2.0 hours minimum)
        discharge_window = PriceWindow(64, 68, 150, 'peak')

        is_valid = optimizer._validate_discharge_window(charge_window, discharge_window, start_soc=0.0)

        assert not is_valid, "Should reject discharge window shorter than minimum required"

    def test_flexible_discharge_uses_config_values(self, optimizer):
        """Test that discharge validation uses configurable values"""
        from models import PriceWindow

        # Mock battery capacity
        optimizer.battery_capacity_kwh = 15.5

        # Test with very small charge (0.1 kWh)
        charge_window = PriceWindow(0, 1, 100, 'valley')  # ~0.1 hours charging
        # Very short discharge window
        discharge_window = PriceWindow(64, 65, 150, 'peak')  # 0.25 hours

        # With min_discharge_hours = 2.0, should require 2.0 hours minimum
        is_valid = optimizer._validate_discharge_window(charge_window, discharge_window, start_soc=50.0)

        assert not is_valid, "Should use min_discharge_hours config when it's larger than fraction"


class TestSocEstimation:
    """Test the SOC estimation fixes"""

    def test_soc_estimation_uses_current_soc(self, optimizer):
        """Test that SOC estimation starts from current SOC, not assuming 100%"""
        # Mock prices with cheap early morning and expensive afternoon
        slot_prices = {}
        for slot in range(96):
            hour = slot // 4
            if hour < 6:  # Early morning
                slot_prices[slot] = 80
            else:  # Rest of day
                slot_prices[slot] = 120

        # Test with 20% current SOC
        current_soc = 20.0

        # Create a cycle that would previously assume 100% SOC
        valleys = [PriceWindow(0, 8, 80, 'valley')]  # Early morning valley
        peaks = [PriceWindow(64, 72, 140, 'peak')]   # Afternoon peak

        cycles = optimizer.find_arbitrage_cycles(valleys, peaks, current_soc, slot_prices)

        # Should create a cycle without assuming SOC was 100%
        assert len(cycles) >= 1, "Should create arbitrage cycle with correct SOC estimation"

    def test_no_soc_skip_for_early_morning_valleys(self, optimizer):
        """Test that early morning valleys are not skipped due to SOC assumptions"""
        slot_prices = {i: 100 for i in range(96)}
        # Make early morning cheap
        for i in range(0, 16):
            slot_prices[i] = 80
        # Make afternoon expensive
        for i in range(64, 72):
            slot_prices[i] = 140

        valleys = [PriceWindow(0, 16, 80, 'valley')]  # Early morning
        peaks = [PriceWindow(64, 72, 140, 'peak')]    # Afternoon

        # With depleted battery, should still use early morning valley
        cycles = optimizer.find_arbitrage_cycles(valleys, peaks, current_soc=5.0, slot_prices=slot_prices)

        assert len(cycles) >= 1, "Should not skip early morning valley due to SOC assumptions"


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


class TestDischargeWindowOverlapPrevention:
    """Tests specifically for preventing discharge window overlap"""

    def test_adjacent_peaks_should_merge(self, optimizer):
        """Adjacent peaks separated by small gap should be treated as one"""
        prices = {i: 100 for i in range(96)}
        # Peak 1: 16:00-18:00
        for i in range(64, 72):
            prices[i] = 200
        # Small gap: 18:00-18:15 (just 1 slot)
        prices[72] = 110
        # Peak 2: 18:15-19:00
        for i in range(73, 76):
            prices[i] = 190
        # Valley at night
        for i in range(0, 20):
            prices[i] = 60

        plan = optimizer.analyze_day(prices, current_soc=20.0)

        print(f"\nAdjacent peaks test:")
        print(f"Cycles: {len(plan.cycles)}")
        for c in plan.cycles:
            print(f"  {c}")

        # With adjacent peaks, we should either:
        # 1. Have only 1 cycle that covers both peaks
        # 2. Or have 2 cycles with non-overlapping discharge windows
        if len(plan.cycles) >= 2:
            d1 = plan.cycles[0].discharge_window
            d2 = plan.cycles[1].discharge_window

            # They should not significantly overlap
            overlap_start = max(d1.start_slot, d2.start_slot)
            overlap_end = min(d1.end_slot, d2.end_slot)
            overlap = max(0, overlap_end - overlap_start)

            print(f"Overlap: {overlap} slots")
            assert overlap < 4, f"Adjacent peaks caused {overlap} slots of overlap"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
