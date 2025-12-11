"""Data models for the ESS optimizer"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

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
        # Handle end of day: slot 96 = 24:00 -> 00:00 (midnight)
        if self.end_slot >= SLOTS_PER_DAY:
            return "00:00"
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

