"""Validated configuration. Money inside the controller is always EUR."""

from pathlib import Path
from datetime import date
import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
import yaml


class Settings(BaseModel):
    control_mode: str = "periodic"
    timezone: str = "Europe/Prague"
    interval_minutes: int = 15
    legacy_min_write_interval_seconds: int = Field(default=10, ge=10)
    reserve_percent: float = Field(default=10, ge=10, le=90)
    maximum_percent: float = Field(default=100, ge=10, le=100)
    charge_kw: float = Field(default=5, gt=0)
    discharge_kw: float = Field(default=5, gt=0)
    # Optional user-supplied planning constraint; no inferred household cap.
    import_limit_kw: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    effective_capacity_kwh: float | None = Field(default=None, gt=0)
    legacy_reference_capacity_kwh: float | None = Field(default=None, gt=0)
    capacity_scenarios_kwh: list[float] | None = None
    charge_efficiency: float = Field(default=0.95, gt=0, le=1)
    discharge_efficiency: float = Field(default=0.95, gt=0, le=1)
    wear_eur_per_kwh: float = Field(default=0.045, ge=0)
    telemetry_max_age_minutes: int = 20
    plan_grace_minutes: int = 30
    history_days: int = Field(default=365, ge=1, le=3650)
    exclude_inferred_grid_only_ev: bool = False
    standard_planner: str = "candidate"
    emhass_shadow: bool = False
    publication_hour: int = 15
    tariff_source: str = "bezdodavatele-2026"
    # Local wall-clock VT periods, supplied by the household's HDO schedule.
    high_tariff_periods: list[list[str]] | None = None
    tariff_valid_from: date | None = None
    tariff_valid_until: date | None = None
    # Hold semantics must be observed on the actual inverter, not inferred by model.
    hold_verified: bool = False
    # Never set this implicitly: preserves monitoring when hardware isn't verified.
    device_control_verified: bool = False
    solar_config_path: str = "solar_config.yaml"
    database_path: str = "data/manager.sqlite3"
    price_source: str = "ote"
    entsoe_area: str = "10YCZ-CEPS-----N"

    @model_validator(mode="after")
    def validate_limits(self):
        import math

        if self.capacity_scenarios_kwh is not None and (
            not self.capacity_scenarios_kwh
            or any(not math.isfinite(c) or c <= 0 for c in self.capacity_scenarios_kwh)
        ):
            raise ValueError("Capacity scenarios must be finite positive kWh values")
        if self.control_mode not in {"periodic", "legacy_daily"}:
            raise ValueError("control_mode must be periodic or legacy_daily")
        if self.standard_planner not in {"candidate", "emhass"}:
            raise ValueError("standard_planner must be candidate or emhass")
        if self.standard_planner == "emhass" and self.control_mode != "legacy_daily":
            raise ValueError(
                "EMHASS appliance adapter requires standard window controls"
            )
        if (
            self.control_mode == "legacy_daily"
            and not float(self.maximum_percent).is_integer()
        ):
            raise ValueError("Daily mode maximum_percent must be an integer SOC")
        if self.reserve_percent >= self.maximum_percent:
            raise ValueError("Reserve must be below maximum charge")
        if self.interval_minutes != 15:
            raise ValueError("The device adapter requires 15-minute intervals")
        for pair in self.high_tariff_periods or []:
            if len(pair) != 2:
                raise ValueError("Each VT period needs start and end HH:MM")
            for value in pair:
                from datetime import time

                time.fromisoformat(value)
        if self.price_source not in {"ote", "entsoe"}:
            raise ValueError("price_source must be ote or entsoe")
        return self


def load_settings(path="manager.yaml") -> Settings:
    load_dotenv(Path(".env"), override=False)
    return Settings(**(yaml.safe_load(Path(path).read_text()) or {}))


def credentials():
    return tuple(
        os.environ.get(k, "") for k in ("APP_ID", "APP_SECRET", "SERIAL_NUMBER")
    )
