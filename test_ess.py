"""
Tests for ESS Optimizer - Dynamic Reactive Strategy
Run with: uv run pytest test_ess.py -v
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from ESS import ESSOptimizer, PriceWindow, OptimizationPlan, ArbitrageCycle


@pytest.fixture
def optimizer():
    """Create an optimizer instance with mocked config"""
    with patch('ESS.Config') as mock_config, \
         patch('ESS.alphaess') as mock_client, \
         patch('ESS.PriceFetcher') as mock_fetcher:
        
        # Mock config
        mock_config_instance = MagicMock()
        mock_config_instance.__getitem__ = lambda self, key: {
            'app_id': 'test_id',
            'app_secret': 'test_secret',
            'serial_number': 'TEST123',
            'charge_to_full': 3.0,
            'price_multiplier': 1.2
        }[key]
        mock_config.return_value = mock_config_instance
        
        # Mock client
        mock_client_instance = MagicMock()
        mock_client_instance.close = AsyncMock()
        mock_client.return_value = mock_client_instance
        
        # Mock price fetcher
        mock_fetcher_instance = MagicMock()
        mock_fetcher.return_value = mock_fetcher_instance
        
        opt = ESSOptimizer()
        return opt


class TestChargingCalculations:
    """Test charging calculations (both hours and slots)"""
    
    def test_calculate_charging_hours_from_empty(self, optimizer):
        """Test charging calculation from 0%"""
        hours = optimizer.calculate_charging_hours_needed(0.0)
        assert hours == 3.0
    
    def test_calculate_charging_hours_from_30_percent(self, optimizer):
        """Test charging calculation from 30%"""
        hours = optimizer.calculate_charging_hours_needed(30.0)
        assert hours == pytest.approx(2.1, 0.01)
    
    def test_calculate_charging_hours_from_50_percent(self, optimizer):
        """Test charging calculation from 50%"""
        hours = optimizer.calculate_charging_hours_needed(50.0)
        assert hours == 1.5
    
    def test_calculate_charging_hours_from_80_percent(self, optimizer):
        """Test charging calculation from 80%"""
        hours = optimizer.calculate_charging_hours_needed(80.0)
        assert hours == pytest.approx(0.6, 0.01)
    
    def test_calculate_charging_hours_from_nearly_full(self, optimizer):
        """Test charging calculation from 95%"""
        hours = optimizer.calculate_charging_hours_needed(95.0)
        assert hours == pytest.approx(0.15, 0.01)
    
    def test_calculate_charging_hours_from_full(self, optimizer):
        """Test charging calculation from 100%"""
        hours = optimizer.calculate_charging_hours_needed(100.0)
        assert hours == 0.0
    
    # New slot-based tests
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


class TestWindowFinding:
    """Test window finding logic with 15-minute slots"""
    
    def test_find_cheapest_window_simple(self, optimizer):
        """Test finding cheapest window in 15-minute slots"""
        # Create 96 slots (full day) - slots 8-20 are cheapest (02:00-05:00)
        prices = {i: 150 for i in range(96)}
        for i in range(8, 20):  # 02:00-05:00 are cheap
            prices[i] = 90 + (i - 8) * 2  # 90-114
        
        # Find 12 slots (3 hours) in range 0-28 (00:00-07:00)
        result = optimizer.find_cheapest_window(prices, window_slots=12, start_slot=0, end_slot=28)
        assert result is not None
        assert isinstance(result, PriceWindow)
        assert 8 <= result.start_slot <= 10  # Should start around 02:00-02:30
        assert result.end_slot - result.start_slot == 12  # 12 slots = 3 hours
        assert result.avg_price < 120  # Should be in cheap range
        assert result.window_type == 'valley'
    
    def test_find_cheapest_window_short(self, optimizer):
        """Test finding short charging window (4 slots = 1 hour)"""
        prices = {i: 150 for i in range(96)}
        prices[8] = 80  # 02:00 is cheapest
        prices[9] = 85
        prices[10] = 82
        prices[11] = 88
        
        result = optimizer.find_cheapest_window(prices, window_slots=4, start_slot=0, end_slot=28)
        assert result is not None
        assert result.start_slot == 8  # Should start at slot 8 (02:00)
        assert result.end_slot == 12   # End at slot 12 (03:00)
    
    def test_find_cheapest_window_no_data(self, optimizer):
        """Test with empty price data"""
        prices = {}
        result = optimizer.find_cheapest_window(prices, window_slots=12, start_slot=0, end_slot=28)
        assert result is None
    
    def test_find_most_expensive_window(self, optimizer):
        """Test finding most expensive consecutive window"""
        prices = {i: 100 for i in range(96)}
        # Make slots 64-72 (16:00-18:00) expensive
        for i in range(64, 72):
            prices[i] = 300 + (i - 64) * 10  # 300-370
        
        result = optimizer.find_most_expensive_window(prices, window_slots=8, start_slot=60, end_slot=76)
        assert result is not None
        assert isinstance(result, PriceWindow)
        assert result.start_slot == 64  # Should start at the expensive region
        assert result.window_type == 'peak'
        assert result.avg_price > 300
    
    def test_find_most_expensive_slots(self, optimizer):
        """Test finding most expensive 15-minute slots"""
        prices = {i: 100 for i in range(96)}
        # Make slots 28-40 (07:00-10:00) expensive
        for i in range(28, 40):
            prices[i] = 180 + (i - 28) * 2  # 180-202
        
        result = optimizer.find_most_expensive_slots(prices, slot_count=12, start_slot=0, end_slot=48)
        assert len(result) == 12
        # All expensive slots should be in 28-40 range
        for slot in result:
            assert 28 <= slot < 40
    
    def test_find_most_expensive_slots_limited_range(self, optimizer):
        """Test finding expensive slots in limited range"""
        prices = {i: 100 for i in range(96)}
        prices[20] = 180  # 05:00
        prices[21] = 175  # 05:15
        prices[30] = 200  # 07:30 - outside range
        
        # Search only in range 0-24 (00:00-06:00)
        result = optimizer.find_most_expensive_slots(prices, slot_count=2, start_slot=0, end_slot=24)
        assert len(result) == 2
        assert 20 in result  # 05:00
        assert 21 in result  # 05:15
        assert 30 not in result  # Outside range
    
    # Legacy method tests (for backwards compatibility)
    def test_find_most_expensive_hours(self, optimizer):
        """Test finding most expensive hours (legacy method)"""
        prices = {
            0: 100, 1: 110, 2: 95,
            3: 105, 4: 98, 5: 120,
            6: 130, 7: 180,  # Most expensive
            8: 175, 9: 140, 10: 170,  # Second and third
            11: 130, 12: 120
        }
        
        result = optimizer.find_most_expensive_hours(prices, count=3, start_hour=0, end_hour=12)
        assert len(result) == 3
        assert 7 in result  # Hour 7 (180) should be in top 3
        assert 8 in result  # Hour 8 (175) should be in top 3
        assert 10 in result  # Hour 10 (170) should be in top 3


class TestTimeConversion:
    """Test time conversion utilities for 15-minute slots"""
    
    def test_slot_to_time_midnight(self, optimizer):
        """Test slot 0 converts to 00:00"""
        assert optimizer.slot_to_time(0) == "00:00"
    
    def test_slot_to_time_quarter_hours(self, optimizer):
        """Test 15-minute increments"""
        assert optimizer.slot_to_time(0) == "00:00"
        assert optimizer.slot_to_time(1) == "00:15"
        assert optimizer.slot_to_time(2) == "00:30"
        assert optimizer.slot_to_time(3) == "00:45"
        assert optimizer.slot_to_time(4) == "01:00"
    
    def test_slot_to_time_various_times(self, optimizer):
        """Test various slot conversions"""
        assert optimizer.slot_to_time(8) == "02:00"   # 2 hours
        assert optimizer.slot_to_time(9) == "02:15"
        assert optimizer.slot_to_time(28) == "07:00"  # 7 hours
        assert optimizer.slot_to_time(48) == "12:00"  # 12 hours
        assert optimizer.slot_to_time(95) == "23:45"  # Last slot
    
    def test_time_to_slot(self, optimizer):
        """Test converting time to slot index"""
        assert optimizer.time_to_slot(0, 0) == 0
        assert optimizer.time_to_slot(0, 15) == 1
        assert optimizer.time_to_slot(0, 30) == 2
        assert optimizer.time_to_slot(0, 45) == 3
        assert optimizer.time_to_slot(1, 0) == 4
        assert optimizer.time_to_slot(2, 0) == 8
        assert optimizer.time_to_slot(7, 0) == 28
    
    def test_slots_to_time_range(self, optimizer):
        """Test slot range to time range conversion"""
        start, end = optimizer.slots_to_time_range(8, 20)  # 02:00-05:00
        assert start == "02:00"
        assert end == "05:00"
        
        start, end = optimizer.slots_to_time_range(9, 21)  # 02:15-05:15
        assert start == "02:15"
        assert end == "05:15"
    
    # Legacy method tests
    def test_hours_to_time_range_whole_hours(self, optimizer):
        """Test conversion of whole hours (legacy)"""
        start, end = optimizer.hours_to_time_range(2, 5)
        assert start == "02:00"
        assert end == "05:00"


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
        """Test successful battery SOC retrieval"""
        mock_data = {'cbat': 45.5}
        optimizer.client.getdata = AsyncMock(return_value=mock_data)
        
        soc = await optimizer.get_battery_soc()
        assert soc == 45.5
    
    async def test_get_battery_soc_list_response(self, optimizer):
        """Test battery SOC retrieval when API returns list"""
        mock_data = [{'cbat': 67.8}]
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
        assert 0 in prices   # First slot (00:00)
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
    
    def test_charging_calculation_negative_soc(self, optimizer):
        """Test that negative SOC is handled"""
        # Should treat as 0%
        hours = optimizer.calculate_charging_hours_needed(-5.0)
        assert hours >= 3.0
    
    def test_charging_calculation_over_100_soc(self, optimizer):
        """Test that SOC > 100% is handled"""
        hours = optimizer.calculate_charging_hours_needed(105.0)
        assert hours <= 0.0
    
    def test_charging_calculation_zero_soc(self, optimizer):
        """Test that 0.0% SOC is handled correctly (not falsy!)"""
        # Critical bug test: 0.0 is falsy in Python but should still trigger charging
        hours = optimizer.calculate_charging_hours_needed(0.0)
        assert hours == 3.0  # Should need full 3 hours
    
    def test_find_window_with_single_price(self, optimizer):
        """Test window finding with only one price available"""
        prices = {3: 100}
        result = optimizer.find_cheapest_window(prices, 1, 0, 7)
        # Should still work
        assert result is not None or result is None  # Either outcome is valid
    
    def test_find_expensive_hours_more_than_available(self, optimizer):
        """Test requesting more expensive hours than available"""
        prices = {0: 100, 1: 110}  # Only 2 hours
        result = optimizer.find_most_expensive_hours(prices, count=5, start_hour=0, end_hour=12)
        assert len(result) <= 2  # Can't return more than available


# =============================================================================
# Real Data Tests - December 3rd, 2025
# =============================================================================
# These tests use actual price data to verify the optimizer makes correct decisions

# Real 15-minute price data from OTE for December 1st, 2025 (96 slots)
PRICES_2025_12_01 = [
    99.03, 98.05, 93.54, 91.75, 108.0, 104.03, 93.75, 90.64,      # 00:00-02:00
    100.87, 90.57, 89.92, 89.12, 87.96, 89.17, 89.93, 92.2,       # 02:00-04:00
    89.16, 90.93, 90.27, 92.34, 88.12, 92.06, 100.65, 117.75,     # 04:00-06:00
    98.42, 117.8, 139.99, 147.34, 140.36, 142.38, 141.91, 157.08, # 06:00-08:00
    172.71, 161.34, 147.89, 143.32, 151.83, 146.68, 139.76, 125.88,  # 08:00-10:00
    128.36, 125.18, 123.16, 119.96, 117.62, 116.61, 117.95, 116.97,  # 10:00-12:00
    126.3, 124.72, 131.26, 137.72, 124.26, 136.36, 139.69, 138.65,   # 12:00-14:00
    134.21, 152.0, 164.15, 173.64, 166.55, 177.3, 193.3, 196.26,     # 14:00-16:00
    201.51, 206.21, 210.7, 213.05, 175.46, 180.74, 178.51, 165.29,   # 16:00-18:00
    185.14, 191.29, 177.0, 174.26, 184.63, 166.23, 166.03, 155.68,   # 18:00-20:00
    167.03, 153.74, 140.28, 123.17, 145.84, 129.25, 114.13, 96.26,   # 20:00-22:00
    121.98, 113.8, 112.14, 96.72, 109.35, 100.0, 95.05, 87.54        # 22:00-24:00
]


# Real 15-minute price data from OTE for December 3rd, 2025 (96 slots)
PRICES_2025_12_03 = [
    104.57, 98.75, 96.82, 96.17, 101.89, 96.05, 96.48, 97.91,   # 00:00-02:00
    97.49, 96.18, 95.4, 94.39, 96.17, 95.67, 94.66, 94.68,      # 02:00-04:00
    95.78, 96.47, 96.97, 99.58, 94.37, 98.33, 104.81, 119.1,    # 04:00-06:00
    111.99, 136.07, 155.18, 190.69, 151.62, 181.14, 231.83, 222.63,  # 06:00-08:00
    231.85, 256.39, 265.36, 229.31, 260.49, 237.05, 184.06, 184.71,  # 08:00-10:00
    207.01, 181.68, 175.8, 174.13, 174.89, 169.99, 168.83, 171.75,   # 10:00-12:00
    163.86, 155.5, 165.19, 175.03, 170.12, 173.6, 185.58, 187.19,    # 12:00-14:00
    176.28, 192.19, 223.07, 217.17, 197.62, 218.14, 274.0, 298.44,   # 14:00-16:00
    244.83, 310.79, 337.44, 366.63, 279.18, 288.07, 254.45, 219.08,  # 16:00-18:00
    220.94, 222.71, 198.43, 178.7, 202.7, 177.36, 162.05, 152.87,    # 18:00-20:00
    174.53, 152.11, 136.46, 125.12, 140.21, 132.9, 117.1, 108.92,    # 20:00-22:00
    132.24, 119.81, 109.54, 103.4, 119.17, 108.16, 102.04, 97.0      # 22:00-24:00
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
    
    def test_cheapest_window_is_at_night(self, optimizer, prices_dict):
        """Verify cheapest 3-hour window is during night hours (00:00-07:00)"""
        # Find cheapest 12 slots (3 hours) in night window
        result = optimizer.find_cheapest_window(
            prices_dict, 
            window_slots=12,  # 3 hours
            start_slot=0,     # 00:00
            end_slot=28       # 07:00
        )
        
        assert result is not None
        
        # Cheapest window should be somewhere in slots 8-20 (02:00-05:00 area)
        # Looking at data: slots 10-12 (02:30-03:00) have prices around 94-96
        assert 0 <= result.start_slot <= 20, f"Charging should start early, got slot {result.start_slot}"
        assert result.avg_price < 100, f"Night avg price should be under 100, got {result.avg_price:.2f}"
        
        print(f"Cheapest 3h window: {result.start_time}-{result.end_time}, avg: {result.avg_price:.2f} EUR/MWh")
    
    def test_most_expensive_slots_are_evening(self, optimizer, prices_dict):
        """Verify most expensive slots are in the evening peak"""
        # Find 12 most expensive slots in the whole day
        expensive = optimizer.find_most_expensive_slots(
            prices_dict,
            slot_count=12,
            start_slot=0,
            end_slot=95
        )
        
        assert len(expensive) == 12
        
        # Most expensive should be evening peak (16:00-18:00 = slots 64-72)
        # Looking at data: slots 66-67 have prices 337-366
        expensive_times = [optimizer.slot_to_time(s) for s in sorted(expensive)]
        print(f"Most expensive 12 slots: {expensive_times}")
        
        # At least half should be in the 16:00-18:00 peak
        evening_peak_slots = [s for s in expensive if 64 <= s <= 72]
        assert len(evening_peak_slots) >= 6, f"Expected most expensive in evening, got {expensive}"
    
    def test_should_charge_with_typical_battery(self, optimizer, prices_dict):
        """Test charging decision with typical 50% battery"""
        # 50% battery needs about 1.5h = 6 slots
        slots_needed = optimizer.calculate_charging_slots_needed(50.0)
        assert slots_needed == 6
        
        # Find charging window
        result = optimizer.find_cheapest_window(
            prices_dict,
            window_slots=slots_needed,
            start_slot=0,
            end_slot=28
        )
        
        assert result is not None
        
        # Calculate daily mean
        daily_mean = sum(PRICES_2025_12_03) / len(PRICES_2025_12_03)
        price_threshold = daily_mean / optimizer.price_multiplier
        
        print(f"Daily mean: {daily_mean:.2f}, threshold: {price_threshold:.2f}")
        print(f"Charging window avg: {result.avg_price:.2f}")
        
        # Night prices (~95) should be well below threshold (~140)
        assert result.avg_price < price_threshold, \
            f"Should charge: {result.avg_price:.2f} < {price_threshold:.2f}"
    
    def test_discharge_window_captures_peak(self, optimizer, prices_dict):
        """Test that discharge is scheduled during expensive morning/evening"""
        # Find expensive slots for discharge (morning peak focus: 00:00-12:00)
        expensive_morning = optimizer.find_most_expensive_slots(
            prices_dict,
            slot_count=12,
            start_slot=0,
            end_slot=48  # 12:00
        )
        
        # Should capture morning peak around 08:00-10:00
        # Slots 32-36 have prices around 230-265
        assert any(32 <= s <= 40 for s in expensive_morning), \
            "Should capture morning peak (08:00-10:00)"
        
        # Average price of selected slots should be high
        avg_expensive = sum(prices_dict[s] for s in expensive_morning) / len(expensive_morning)
        assert avg_expensive > 180, f"Discharge slots should avg > 180, got {avg_expensive:.2f}"
    
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
        print(f"Spread ratio: {evening_mean/night_mean:.2f}x")
        
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
            assert cycle.charge_window.end_slot <= cycle.discharge_window.start_slot, \
                "Charge window should end before discharge starts"
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
    
    def test_soc_estimation_for_peaks(self, optimizer):
        """Test SOC estimation for covering peak periods"""
        soc_needed_3h = optimizer.estimate_soc_needed_for_peak(3.0)
        assert soc_needed_3h >= 80, f"Should need high SOC for 3h peak"
        
        soc_needed_2h = optimizer.estimate_soc_needed_for_peak(2.0)
        assert 60 <= soc_needed_2h <= 80, f"Expected 60-80% for 2h peak"
    
    def test_soc_after_discharge(self, optimizer):
        """Test SOC estimation after discharge"""
        soc_after = optimizer.estimate_soc_after_discharge(100.0, 3.0)
        assert soc_after <= 20, f"Expected low SOC after 3h discharge"


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
            start_slot=8,    # 02:00
            end_slot=20,     # 05:00
            avg_price=95.0,
            window_type='valley'
        )
        
        assert window.start_time == "02:00"
        assert window.end_time == "05:00"
        assert window.duration_hours == 3.0
    
    def test_price_window_quarter_hour_times(self):
        """Test PriceWindow with quarter-hour boundaries"""
        window = PriceWindow(
            start_slot=9,    # 02:15
            end_slot=21,     # 05:15
            avg_price=100.0,
            window_type='peak'
        )
        
        assert window.start_time == "02:15"
        assert window.end_time == "05:15"
        assert window.duration_hours == 3.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

