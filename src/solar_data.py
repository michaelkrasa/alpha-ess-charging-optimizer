"""Solar generation + irradiance/temperature data access."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import aiohttp
import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SolarConfig:
    """Configuration for Open-Meteo irradiance + temperature data."""

    latitude: float
    longitude: float
    tilt: float = 0.0
    azimuth: float = 0.0
    timezone: str = "UTC"
    gti_variable: str = "global_tilted_irradiance"
    temperature_variable: str = "temperature_2m"
    granularity: str = "minutely_15"  # "minutely_15" or "hourly"
    open_meteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    pv_forecast_enabled: bool = False
    pv_capacity_kw: Optional[float] = None
    pv_inverter_kw: Optional[float] = None
    pv_derate: float = 0.85
    pv_temp_coeff_per_c: float = -0.004
    pv_temp_ref_c: float = 25.0
    pv_noct_c: float = 45.0
    pv_forecast_conservative_factor: float = 0.7
    pv_scale_adaptive: bool = False
    pv_scale_days: int = 89
    pv_scale_min_gti: float = 150.0
    pv_scale_min_samples: int = 200
    pv_scale_cache_path: str = "data/pv_scale_cache.json"
    pv_scale_refresh_hours: int = 24
    pv_scale_max_workers: int = 3

    @classmethod
    def from_yaml(cls, path: str) -> "SolarConfig":
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)


@dataclass(frozen=True)
class SolarWeatherPoint:
    timestamp: datetime
    gti_w_per_m2: float
    temperature_c: float


@dataclass(frozen=True)
class AlphaESSSolarPoint:
    timestamp: datetime
    pv_power_kw: float
    raw: dict


class OpenMeteoClient:
    """Open-Meteo API access for GTI + temperature."""

    def __init__(
        self,
        config: SolarConfig,
        *,
        session: aiohttp.ClientSession | None = None,
        timeout: int = 30,
    ) -> None:
        self.config = config
        self._timeout = timeout
        self._session = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout))
        self._owns_session = session is None

    async def fetch_gti_temperature(
        self,
        start_date: date,
        end_date: date,
        *,
        granularity: Optional[str] = None,
    ) -> list[SolarWeatherPoint]:
        """Fetch GTI + temperature for a date range.

        Returns a list of SolarWeatherPoint aligned to the API's time series.
        """
        granularity = granularity or self.config.granularity
        if granularity not in {"minutely_15", "hourly"}:
            raise ValueError("granularity must be 'minutely_15' or 'hourly'")

        params = {
            "latitude": self.config.latitude,
            "longitude": self.config.longitude,
            "tilt": self.config.tilt,
            "azimuth": self.config.azimuth,
            "timezone": self.config.timezone,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
        }

        variables = f"{self.config.gti_variable},{self.config.temperature_variable}"
        params[granularity] = variables

        async with self._session.get(self.config.open_meteo_base_url, params=params) as response:
            response.raise_for_status()
            payload = await response.json()

        series = payload.get(granularity)
        if not series:
            logger.warning("Open-Meteo response missing '%s' data", granularity)
            return []

        times = series.get("time", [])
        gti = series.get(self.config.gti_variable, [])
        temp = series.get(self.config.temperature_variable, [])

        points: list[SolarWeatherPoint] = []
        timezone = ZoneInfo(self.config.timezone)

        for idx, time_str in enumerate(times):
            timestamp = _parse_timestamp(time_str, timezone, None)
            if timestamp is None:
                continue

            gti_val = _safe_float(gti[idx]) if idx < len(gti) else None
            temp_val = _safe_float(temp[idx]) if idx < len(temp) else None
            if gti_val is None or temp_val is None:
                continue

            points.append(SolarWeatherPoint(timestamp=timestamp, gti_w_per_m2=gti_val, temperature_c=temp_val))

        return points

    async def close(self) -> None:
        if self._owns_session:
            await self._session.close()


class SolarForecaster:
    """Simple PV forecaster using Open-Meteo GTI + temperature."""

    def __init__(self, config: SolarConfig) -> None:
        self.config = config
        self.client = OpenMeteoClient(config)

    def estimate_pv_power_kw(
        self,
        gti_w_per_m2: float,
        temperature_c: float,
        *,
        capacity_kw_override: Optional[float] = None,
        derate_override: Optional[float] = None,
    ) -> float:
        """Estimate PV power from GTI and temperature."""
        capacity_kw = capacity_kw_override if capacity_kw_override is not None else self.config.pv_capacity_kw
        if not capacity_kw or capacity_kw <= 0:
            return 0.0

        irradiance_factor = max(0.0, gti_w_per_m2) / 1000.0

        # Simple cell temperature estimate (NOCT-based)
        temp_cell = temperature_c + ((self.config.pv_noct_c - 20.0) / 800.0) * max(0.0, gti_w_per_m2)
        temp_factor = 1.0 + self.config.pv_temp_coeff_per_c * (temp_cell - self.config.pv_temp_ref_c)

        derate = derate_override if derate_override is not None else self.config.pv_derate
        power_kw = capacity_kw * irradiance_factor * derate * temp_factor
        if self.config.pv_inverter_kw:
            power_kw = min(power_kw, self.config.pv_inverter_kw)

        return max(0.0, power_kw)

    async def forecast_power(
        self,
        start_date: date,
        end_date: date,
        *,
        capacity_kw_override: Optional[float] = None,
        derate_override: Optional[float] = None,
    ) -> list[AlphaESSSolarPoint]:
        """Forecast PV power for a date range."""
        if not self.config.pv_forecast_enabled:
            return []
        if capacity_kw_override is None and not self.config.pv_capacity_kw:
            logger.warning("PV forecast enabled but pv_capacity_kw is not set")
            return []

        weather = await self.client.fetch_gti_temperature(start_date, end_date)
        points: list[AlphaESSSolarPoint] = []
        for entry in weather:
            power_kw = self.estimate_pv_power_kw(
                entry.gti_w_per_m2,
                entry.temperature_c,
                capacity_kw_override=capacity_kw_override,
                derate_override=derate_override,
            )
            points.append(
                AlphaESSSolarPoint(
                    timestamp=entry.timestamp,
                    pv_power_kw=power_kw,
                    raw={"gti_w_per_m2": entry.gti_w_per_m2, "temperature_c": entry.temperature_c},
                )
            )
        return points

    async def close(self) -> None:
        await self.client.close()


def extract_pv_power_series(
    raw: Iterable[dict] | None,
    *,
    timezone: str | ZoneInfo,
    timestamp_key: str,
    power_key: str,
    base_date: date | None = None,
    power_unit: str = "W",
) -> list[AlphaESSSolarPoint]:
    """Extract PV power series from AlphaESS payload.

    Args:
        raw: Raw payload list from the API.
        timezone: IANA timezone string or ZoneInfo.
        timestamp_key: Key containing the timestamp (e.g. "time" or "timestamp").
        power_key: Key containing PV power (e.g. "ppv" or "pvPower").
        base_date: If timestamps are time-only (HH:MM), this date is used.
        power_unit: "W" or "kW". Values are normalized to kW.
    """
    if not raw:
        return []

    tzinfo = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    points: list[AlphaESSSolarPoint] = []
    for entry in raw:
        if timestamp_key not in entry or power_key not in entry:
            continue

        timestamp = _parse_timestamp(entry[timestamp_key], tzinfo, base_date)
        if timestamp is None:
            continue

        power_val = _safe_float(entry[power_key])
        if power_val is None:
            continue

        if power_unit.lower() == "w":
            power_val = power_val / 1000.0

        points.append(AlphaESSSolarPoint(timestamp=timestamp, pv_power_kw=power_val, raw=entry))

    return points


def aggregate_power_series(
    raw: Iterable[dict] | None,
    *,
    timezone: str | ZoneInfo,
    timestamp_key: str = "uploadTime",
    power_key: str = "ppv",
    power_unit: str = "W",
    interval_minutes: int = 15,
) -> list[AlphaESSSolarPoint]:
    """Aggregate raw AlphaESS power data into interval buckets."""
    if not raw:
        return []

    tzinfo = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    buckets: dict[datetime, list[float]] = {}

    for entry in raw:
        if timestamp_key not in entry or power_key not in entry:
            continue

        timestamp = _parse_timestamp(entry[timestamp_key], tzinfo, None)
        if timestamp is None:
            continue

        power_val = _safe_float(entry[power_key])
        if power_val is None:
            continue

        if power_unit.lower() == "w":
            power_val = power_val / 1000.0

        bucket_minute = (timestamp.minute // interval_minutes) * interval_minutes
        bucket_time = timestamp.replace(minute=bucket_minute, second=0, microsecond=0)
        buckets.setdefault(bucket_time, []).append(power_val)

    points: list[AlphaESSSolarPoint] = []
    for bucket_time in sorted(buckets.keys()):
        values = buckets[bucket_time]
        avg_power = sum(values) / len(values)
        points.append(AlphaESSSolarPoint(timestamp=bucket_time, pv_power_kw=avg_power, raw={"count": len(values)}))

    return points


def power_series_to_slot_kwh(
    points: Iterable[AlphaESSSolarPoint],
    *,
    target_date: date,
    slot_minutes: int = 15,
    timezone: str | ZoneInfo,
) -> dict[int, float]:
    """Convert power series to kWh per slot for a given date."""
    tzinfo = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    slot_kwh: dict[int, float] = {}
    minutes_per_day = 24 * 60
    slots_per_day = minutes_per_day // slot_minutes

    for point in points:
        timestamp = point.timestamp.astimezone(tzinfo)
        if timestamp.date() != target_date:
            continue
        slot = (timestamp.hour * 60 + timestamp.minute) // slot_minutes
        if slot < 0 or slot >= slots_per_day:
            continue
        kwh = point.pv_power_kw * (slot_minutes / 60.0)
        slot_kwh[slot] = slot_kwh.get(slot, 0.0) + kwh

    return slot_kwh


def expand_hourly_points_to_quarters(
    points: Iterable[AlphaESSSolarPoint],
) -> list[AlphaESSSolarPoint]:
    """Expand hourly PV points into 15-minute points with equal power."""
    expanded: list[AlphaESSSolarPoint] = []
    for point in points:
        base = point.timestamp.replace(minute=0, second=0, microsecond=0)
        for offset in (0, 15, 30, 45):
            expanded.append(
                AlphaESSSolarPoint(
                    timestamp=base.replace(minute=offset),
                    pv_power_kw=point.pv_power_kw,
                    raw=point.raw,
                )
            )
    return expanded


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value, timezone: ZoneInfo, base_date: date | None) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone)

    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone)

    if isinstance(value, (int, float)):
        # Assume unix seconds
        return datetime.fromtimestamp(value, tz=timezone)

    if isinstance(value, str):
        value = value.strip()
        if len(value) <= 5 and ":" in value:
            if base_date is None:
                return None
            hour, minute = value.split(":", 1)
            return datetime(
                base_date.year,
                base_date.month,
                base_date.day,
                int(hour),
                int(minute),
                tzinfo=timezone,
            )

        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone)
            return parsed
        except ValueError:
            return None

    return None
