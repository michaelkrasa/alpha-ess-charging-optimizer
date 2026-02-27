#!/usr/bin/env python3
"""Fetch and align AlphaESS PV data with Open-Meteo GTI/temperature.

Calibrates a simple PV model using the last N days of data.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import re
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from config import Config
from src.ess_client import ESSClient
from src.solar_data import (
    OpenMeteoClient,
    SolarConfig,
    aggregate_power_series,
    power_series_to_slot_kwh,
)

DEFAULT_DAYS = 89
DEFAULT_MAX_WORKERS = 3
DEFAULT_MIN_GTI = 50.0


def _daterange(start: date, end: date) -> list[date]:
    days = (end - start).days
    return [start + timedelta(days=i) for i in range(days + 1)]


def _estimate_temp_cell(temp_c: float, gti_w_m2: float, noct_c: float) -> float:
    # Simple NOCT-based estimate
    return temp_c + ((noct_c - 20.0) / 800.0) * max(0.0, gti_w_m2)


def _estimate_power_kw(
    gti_w_m2: float,
    temp_c: float,
    *,
    capacity_kw: float,
    derate: float,
    temp_coeff_per_c: float,
    temp_ref_c: float,
    noct_c: float,
    inverter_kw: float | None,
) -> float:
    irradiance_factor = max(0.0, gti_w_m2) / 1000.0
    temp_cell = _estimate_temp_cell(temp_c, gti_w_m2, noct_c)
    temp_factor = 1.0 + temp_coeff_per_c * (temp_cell - temp_ref_c)
    power_kw = capacity_kw * irradiance_factor * derate * temp_factor
    if inverter_kw:
        power_kw = min(power_kw, inverter_kw)
    return max(0.0, power_kw)


async def _fetch_alphaess_day(
    client: ESSClient,
    target_date: date,
    timezone: ZoneInfo,
    semaphore: asyncio.Semaphore,
) -> tuple[date, dict[int, float], int]:
    async with semaphore:
        raw = await client.get_one_day_power(target_date)
    if not raw:
        return target_date, {}, 0
    points = aggregate_power_series(raw, timezone=timezone)
    slot_kwh = power_series_to_slot_kwh(points, target_date=target_date, timezone=timezone, slot_minutes=15)
    return target_date, slot_kwh, len(raw)


def _build_weather_index(points) -> dict[datetime, tuple[float, float]]:
    index: dict[datetime, tuple[float, float]] = {}
    for p in points:
        index[p.timestamp] = (p.gti_w_per_m2, p.temperature_c)
    return index


def _compute_metrics(actual: list[float], predicted: list[float]) -> dict[str, float]:
    if not actual:
        return {"count": 0}
    n = len(actual)
    mae = sum(abs(a - p) for a, p in zip(actual, predicted)) / n
    mse = sum((a - p) ** 2 for a, p in zip(actual, predicted)) / n
    rmse = math.sqrt(mse)
    mean_actual = sum(actual) / n
    ss_tot = sum((a - mean_actual) ** 2 for a in actual)
    ss_res = sum((a - p) ** 2 for a, p in zip(actual, predicted))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    return {"count": n, "mae": mae, "rmse": rmse, "r2": r2}


def _replace_yaml_value(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^({re.escape(key)}\s*:\s*).*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(lambda m: f"{m.group(1)}{value}", text, count=1)
    return text + ("\n" if not text.endswith("\n") else "") + f"{key}: {value}\n"


def _apply_calibration_to_config(
    path: Path,
    *,
    pv_capacity_kw: float | None,
    pv_derate: float | None,
    enable: bool,
) -> None:
    text = path.read_text()
    text = _replace_yaml_value(text, "pv_forecast_enabled", "true" if enable else "false")
    if pv_capacity_kw is not None:
        text = _replace_yaml_value(text, "pv_capacity_kw", f"{pv_capacity_kw:.3f}")
    if pv_derate is not None:
        text = _replace_yaml_value(text, "pv_derate", f"{pv_derate:.3f}")
    path.write_text(text)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate PV forecast using AlphaESS + Open-Meteo data")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Number of days to fetch (default: 89)")
    parser.add_argument("--start-date", type=str, help="Start date YYYY-MM-DD (overrides --days)")
    parser.add_argument("--end-date", type=str, help="End date YYYY-MM-DD (overrides --days)")
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS, help="Concurrent AlphaESS requests")
    parser.add_argument("--min-gti", type=float, default=DEFAULT_MIN_GTI, help="Min GTI to use in calibration")
    parser.add_argument("--output-csv", type=str, default="data/solar_calibration.csv")
    parser.add_argument("--output-json", type=str, default="data/solar_calibration_report.json")
    parser.add_argument("--apply", action="store_true", help="Apply calibrated values to solar_config.yaml")
    args = parser.parse_args()

    solar_config_path = Path("solar_config.yaml")
    solar_config = SolarConfig.from_yaml(str(solar_config_path))
    tz = ZoneInfo(solar_config.timezone)

    if args.start_date and args.end_date:
        start_date = date.fromisoformat(args.start_date)
        end_date = date.fromisoformat(args.end_date)
    else:
        end_date = datetime.now(tz).date()
        start_date = end_date - timedelta(days=max(1, args.days) - 1)

    dates = _daterange(start_date, end_date)

    config = Config("config.yaml")
    ess = ESSClient(
        config["app_id"],
        config["app_secret"],
        config["serial_number"],
        int(config.get("min_soc", 10)),
        int(config.get("max_soc", 100)),
    )

    semaphore = asyncio.Semaphore(args.max_workers)

    tasks = [
        _fetch_alphaess_day(ess, target_date, tz, semaphore)
        for target_date in dates
    ]
    alpha_results = await asyncio.gather(*tasks)

    alpha_by_date: dict[date, dict[int, float]] = {}
    raw_counts: dict[date, int] = {}
    for target_date, slot_kwh, raw_count in alpha_results:
        alpha_by_date[target_date] = slot_kwh
        raw_counts[target_date] = raw_count

    await ess.close()

    meteo = OpenMeteoClient(solar_config)
    weather_points = await meteo.fetch_gti_temperature(start_date, end_date)
    await meteo.close()

    weather_index = _build_weather_index(weather_points)

    output_csv = Path(args.output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    actual_kw: list[float] = []
    baseline_kw: list[float] = []
    base_no_capacity: list[float] = []
    calibration_samples: list[tuple[float, float, float]] = []

    pv_capacity_kw = solar_config.pv_capacity_kw or 0.0
    pv_derate = solar_config.pv_derate

    with output_csv.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp",
                "date",
                "slot",
                "gti_w_m2",
                "temp_c",
                "pv_kw_actual",
                "pv_kwh_actual",
                "pv_kw_baseline",
            ],
        )
        writer.writeheader()

        for day in dates:
            slot_map = alpha_by_date.get(day, {})
            if not slot_map:
                continue

            for slot, kwh in slot_map.items():
                ts = datetime.combine(day, time(0, 0), tzinfo=tz) + timedelta(minutes=slot * 15)
                weather = weather_index.get(ts)
                if not weather:
                    continue
                gti, temp_c = weather
                pv_kw_actual = kwh * 4.0

                pv_kw_baseline = 0.0
                if pv_capacity_kw > 0:
                    pv_kw_baseline = _estimate_power_kw(
                        gti,
                        temp_c,
                        capacity_kw=pv_capacity_kw,
                        derate=pv_derate,
                        temp_coeff_per_c=solar_config.pv_temp_coeff_per_c,
                        temp_ref_c=solar_config.pv_temp_ref_c,
                        noct_c=solar_config.pv_noct_c,
                        inverter_kw=solar_config.pv_inverter_kw,
                    )

                writer.writerow(
                    {
                        "timestamp": ts.isoformat(),
                        "date": day.isoformat(),
                        "slot": slot,
                        "gti_w_m2": f"{gti:.2f}",
                        "temp_c": f"{temp_c:.2f}",
                        "pv_kw_actual": f"{pv_kw_actual:.4f}",
                        "pv_kwh_actual": f"{kwh:.4f}",
                        "pv_kw_baseline": f"{pv_kw_baseline:.4f}",
                    }
                )

                if gti >= args.min_gti:
                    temp_cell = _estimate_temp_cell(temp_c, gti, solar_config.pv_noct_c)
                    temp_factor = 1.0 + solar_config.pv_temp_coeff_per_c * (temp_cell - solar_config.pv_temp_ref_c)
                    base_no_capacity.append((gti / 1000.0) * temp_factor)
                    actual_kw.append(pv_kw_actual)
                    baseline_kw.append(pv_kw_baseline)
                    calibration_samples.append((gti, temp_c, pv_kw_actual))

    # Calibration
    effective_capacity = None
    calibrated_derate = None

    # Filter out likely clipping if inverter is set
    filtered_actual = []
    filtered_base = []
    for a, b in zip(actual_kw, base_no_capacity):
        if solar_config.pv_inverter_kw and a >= solar_config.pv_inverter_kw * 0.98:
            continue
        filtered_actual.append(a)
        filtered_base.append(b)

    if filtered_base:
        numerator = sum(a * b for a, b in zip(filtered_actual, filtered_base))
        denominator = sum(b ** 2 for b in filtered_base)
        if denominator > 0:
            effective_capacity = numerator / denominator

    if pv_capacity_kw > 0 and effective_capacity is not None:
        calibrated_derate = effective_capacity / pv_capacity_kw

    metrics_baseline = _compute_metrics(actual_kw, baseline_kw)

    calibrated_metrics = {"count": 0}
    if effective_capacity is not None:
        calibrated_pred = []
        for gti, temp_c, _actual in calibration_samples:
            calibrated_pred.append(
                _estimate_power_kw(
                    gti,
                    temp_c,
                    capacity_kw=effective_capacity,
                    derate=1.0,
                    temp_coeff_per_c=solar_config.pv_temp_coeff_per_c,
                    temp_ref_c=solar_config.pv_temp_ref_c,
                    noct_c=solar_config.pv_noct_c,
                    inverter_kw=solar_config.pv_inverter_kw,
                )
            )
        calibrated_metrics = _compute_metrics(actual_kw, calibrated_pred)

    report = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": len(dates),
        "alphaess_records": {d.isoformat(): raw_counts.get(d, 0) for d in dates},
        "min_gti": args.min_gti,
        "effective_capacity_kw": effective_capacity,
        "calibrated_derate": calibrated_derate,
        "baseline_metrics": metrics_baseline,
        "calibrated_metrics": calibrated_metrics,
    }

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2))

    if args.apply:
        apply_capacity = None
        apply_derate = None

        if effective_capacity is not None:
            if pv_capacity_kw > 0:
                apply_capacity = pv_capacity_kw
                apply_derate = calibrated_derate
            else:
                apply_derate = solar_config.pv_derate
                if apply_derate and apply_derate > 0:
                    apply_capacity = effective_capacity / apply_derate
                else:
                    apply_capacity = effective_capacity
                    apply_derate = 1.0

        _apply_calibration_to_config(
            solar_config_path,
            pv_capacity_kw=apply_capacity,
            pv_derate=apply_derate,
            enable=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
