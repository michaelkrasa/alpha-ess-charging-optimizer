from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
PRAGUE = ZoneInfo("Europe/Prague")


def utc_now():
    return datetime.now(UTC)


def day_slots(day, tz=PRAGUE):
    """Real instants, including the 92/100 intervals on DST transition days."""
    from datetime import time

    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return [
        start + timedelta(minutes=15 * i)
        for i in range(int((end - start).total_seconds() / 900))
    ]


def next_boundary(now):
    return datetime.fromtimestamp((int(now.timestamp()) // 900 + 1) * 900, UTC)


@dataclass(frozen=True)
class Telemetry:
    timestamp: str
    soc_percent: float
    capacity_kwh: float
    load_kw: float
    solar_kw: float
    grid_kw: float
    battery_kw: float
    inverter: str = "unknown"

    def validate(self, now, max_age_minutes):
        import math

        if any(
            not math.isfinite(v)
            for v in (
                self.soc_percent,
                self.capacity_kwh,
                self.load_kw,
                self.solar_kw,
                self.grid_kw,
                self.battery_kw,
            )
        ):
            raise ValueError("Non-finite telemetry")
        observed = datetime.fromisoformat(self.timestamp)
        if observed.tzinfo is None:
            raise ValueError("Telemetry timestamp must include a timezone")
        age = (now - observed).total_seconds()
        if age < -300 or age > max_age_minutes * 60:
            raise ValueError("Battery telemetry is stale or in the future")
        if (
            not 0 <= self.soc_percent <= 100
            or self.capacity_kwh <= 0
            or min(self.load_kw, self.solar_kw) < 0
        ):
            raise ValueError("Invalid battery or load telemetry")


@dataclass(frozen=True)
class Slot:
    timestamp: str
    spot_eur_mwh: float
    import_eur_kwh: float
    export_eur_kwh: float
    load_kw: float
    solar_kw: float
    tariff: str
    charging_load_ceiling_kw: float | None = None


@dataclass
class Decision:
    timestamp: str
    action: str
    grid_charge_kw: float
    battery_load_kw: float
    solar_charge_kw: float
    soc_percent: float
    import_eur_kwh: float
    export_eur_kwh: float
    load_kw: float
    solar_kw: float
    reason: str
    charge_minutes: int | None = None

    def dict(self):
        return asdict(self)
