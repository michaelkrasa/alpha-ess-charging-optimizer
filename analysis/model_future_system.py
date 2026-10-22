"""Model future load and candidate PV/battery systems.

The model uses the measured 2025 household load shape, adds an expected
3,000 kWh/year for a second plug-in vehicle, and combines it with hourly PVGIS
production profiles for north- and south-facing arrays near postal code 25242.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd


LATITUDE = 49.9681
LONGITUDE = 14.5135
MEASURED_2025_LOAD_KWH = 16073.7
SECOND_VEHICLE_KWH = 3000.0
ROUND_TRIP_EFFICIENCY = 0.90


def hourly_load_profile(path: Path) -> pd.Series:
    raw = pd.read_csv(path, parse_dates=["timestamp"]).drop_duplicates("timestamp")
    raw = raw.set_index("timestamp").sort_index()
    hourly = (raw["load_kw"] / 12).resample("h").sum(min_count=1)
    typical = hourly.groupby([hourly.index.month, hourly.index.hour]).median()
    missing = hourly.isna() | (hourly <= 0)
    for timestamp in hourly.index[missing]:
        hourly.loc[timestamp] = typical.loc[(timestamp.month, timestamp.hour)]

    # Reconcile interval integration to the more reliable API daily totals.
    hourly *= MEASURED_2025_LOAD_KWH / hourly.sum()

    # Expected wall energy for 12,000 electric km/year in a large PHEV.
    daily_vehicle_energy = SECOND_VEHICLE_KWH / 365
    charging_hours = (21, 22, 23, 0)
    for timestamp in hourly.index:
        if timestamp.hour in charging_hours:
            hourly.loc[timestamp] += daily_vehicle_energy / len(charging_hours)
    return hourly


def pvgis_hourly_per_kwp(aspect: int) -> np.ndarray:
    params = {
        "lat": LATITUDE,
        "lon": LONGITUDE,
        "startyear": 2023,
        "endyear": 2023,
        "pvcalculation": 1,
        "peakpower": 1,
        "loss": 14,
        "angle": 20,
        "aspect": aspect,
        "outputformat": "json",
    }
    url = "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc?" + urlencode(params)
    with urlopen(url, timeout=60) as response:
        payload = json.load(response)
    return np.array([point["P"] / 1000 for point in payload["outputs"]["hourly"]])


def simulate(
    load: np.ndarray,
    pv: np.ndarray,
    battery_usable_kwh: float,
    battery_power_kw: float,
) -> dict[str, float]:
    charge_efficiency = ROUND_TRIP_EFFICIENCY**0.5
    discharge_efficiency = ROUND_TRIP_EFFICIENCY**0.5
    state = battery_usable_kwh * 0.5
    grid_import = 0.0
    grid_export = 0.0
    charged = 0.0
    direct_solar = 0.0

    for demand, production in zip(load, pv, strict=True):
        direct = min(demand, production)
        direct_solar += direct
        surplus = production - direct
        deficit = demand - direct

        if surplus > 0 and battery_usable_kwh:
            input_energy = min(
                surplus,
                battery_power_kw,
                (battery_usable_kwh - state) / charge_efficiency,
            )
            state += input_energy * charge_efficiency
            charged += input_energy
            surplus -= input_energy
        grid_export += surplus

        if deficit > 0 and battery_usable_kwh:
            output_energy = min(
                deficit,
                battery_power_kw,
                state * discharge_efficiency,
            )
            state -= output_energy / discharge_efficiency
            deficit -= output_energy
        grid_import += deficit

    annual_load = float(load.sum())
    annual_pv = float(pv.sum())
    return {
        "load_kwh": annual_load,
        "pv_kwh": annual_pv,
        "grid_import_kwh": grid_import,
        "grid_export_kwh": grid_export,
        "self_sufficiency_pct": 100 * (1 - grid_import / annual_load),
        "pv_self_consumption_pct": 100 * (1 - grid_export / annual_pv),
        "equivalent_cycles": charged * charge_efficiency / battery_usable_kwh
        if battery_usable_kwh
        else 0,
    }


def main() -> None:
    output_dir = Path("analysis/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    load_series = hourly_load_profile(output_dir / "alphaess_power_2025.csv")
    load = load_series.to_numpy()
    north = pvgis_hourly_per_kwp(180)
    south = pvgis_hourly_per_kwp(0)

    pv_layouts = {
        "14.5 kWp (6 north + 8.5 south)": (6.0, 8.5),
        "17.5 kWp (9 north + 8.5 south)": (9.0, 8.5),
        "20.5 kWp (12 north + 8.5 south)": (12.0, 8.5),
    }
    battery_options = (0, 10, 15, 20, 25, 30)
    results = []
    for layout, (north_kwp, south_kwp) in pv_layouts.items():
        pv = north * north_kwp + south * south_kwp
        for battery_kwh in battery_options:
            power_kw = min(10.0, max(5.0, battery_kwh / 2)) if battery_kwh else 0
            result = simulate(load, pv, battery_kwh, power_kw)
            result.update(
                {
                    "layout": layout,
                    "north_kwp": north_kwp,
                    "south_kwp": south_kwp,
                    "battery_usable_kwh": battery_kwh,
                    "battery_power_kw": power_kw,
                }
            )
            results.append(result)
    frame = pd.DataFrame(results)
    frame.to_csv(output_dir / "system_scenarios.csv", index=False)

    monthly = pd.DataFrame({"future_load_kwh": load}, index=load_series.index)
    monthly["north_pv_per_kwp_kwh"] = north
    monthly["south_pv_per_kwp_kwh"] = south
    monthly = monthly.groupby(monthly.index.month).sum()
    monthly.index.name = "month"
    monthly.to_csv(output_dir / "future_monthly_profile.csv")

    columns = [
        "layout",
        "battery_usable_kwh",
        "load_kwh",
        "pv_kwh",
        "grid_import_kwh",
        "grid_export_kwh",
        "self_sufficiency_pct",
        "pv_self_consumption_pct",
        "equivalent_cycles",
    ]
    print(frame[columns].round(1).to_string(index=False))
    print("\nMonthly future load and PV yield per kWp:")
    print(monthly.round(1).to_string())


if __name__ == "__main__":
    main()
