"""Pure access to the original daily strategy, without credentials or clients."""

from datetime import datetime

from .domain import PRAGUE


def legacy_periods(slots, telemetry, settings):
    from ..battery_manager import BatteryManager
    from ..optimizer import ESSOptimizer
    from ..price_analyzer import PriceAnalyzer

    old = object.__new__(ESSOptimizer)
    old.config = {
        "price_multiplier": 1.18,
        "min_window_slots": 2,
        "smoothing_window": 2,
        "early_morning_end_hour": 5,
        "afternoon_start_hour": 10,
        "afternoon_end_hour": 18,
        "min_discharge_fraction": 0.5,
        "min_discharge_hours": 1.5,
    }
    old.solar_config = None
    old.battery_manager = BatteryManager(
        5.0,
        1.8,
        int(settings.reserve_percent),
        int(settings.maximum_percent),
    )
    old.battery_manager.set_capacity(
        settings.legacy_reference_capacity_kwh or telemetry.capacity_kwh * 0.9
    )
    old.price_analyzer = PriceAnalyzer(1.18, 2, 2)
    old.MIN_WINDOW_SLOTS = 2
    old.DISCHARGE_EXTENSION_THRESHOLD = 0.8
    old.DAYTIME_CHARGE_FACTOR = 0.75
    # One complete future wall-clock cycle. Later published prices replace
    # earlier occurrences; no unknown price or future actual is manufactured.
    values = {}
    solar = {}
    for s in slots:
        t = datetime.fromisoformat(s.timestamp).astimezone(PRAGUE)
        i = t.hour * 4 + t.minute // 15
        values[i] = s.spot_eur_mwh
        solar[i] = s.solar_kw * 0.25
    if len(values) != 96:
        raise ValueError("Original-policy candidate needs a complete known day")
    result = old.analyze_day(values, telemetry.soc_percent, solar)

    def periods(kind):
        pairs = []
        for cycle in result.cycles[:2]:
            w = getattr(cycle, kind + "_window")
            a, b = max(0, w.start_slot), min(95, w.end_slot)
            if a < b:
                pairs.append(
                    (f"{a // 4:02d}:{a % 4 * 15:02d}", f"{b // 4:02d}:{b % 4 * 15:02d}")
                )
        merged = []
        for a, b in sorted(pairs):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        return tuple(merged)

    charge, discharge = periods("charge"), periods("discharge")
    if any(a < d and c < b for a, b in charge for c, d in discharge):
        return (), None
    return charge, discharge if discharge else None
