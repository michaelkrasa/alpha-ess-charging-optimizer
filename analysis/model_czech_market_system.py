"""Price-aware Czech solar, EV, and battery sizing model.

Run with:
    uv run --with scipy python analysis/model_czech_market_system.py

The optimization uses 2025 OTE day-ahead prices, a flexible weekly EV energy
budget, PV curtailment during unattractive export periods, grid charging, and
battery discharge into site load. It does not allow battery export at night.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd
from scipy.optimize import linprog
from scipy.sparse import lil_matrix


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "analysis/output"
PRE_EV_MONTHLY_KWH = np.array([1370, 1138, 872, 908, 760, 719, 605, 773, 729, 797, 1085, 1484], dtype=float)
FUTURE_LOAD_KWH = 19073.7
EV_ENERGY_KWH = FUTURE_LOAD_KWH - PRE_EV_MONTHLY_KWH.sum()

PV_LAYOUTS = {
    "0 kWp": (0.0, 0.0),
    "8 kWp roof": (8.0, 0.0),
    "10 kWp": (8.0, 2.0),
    "12 kWp": (8.0, 4.0),
    "15 kWp": (9.0, 6.0),
    "17.5 kWp": (9.0, 8.5),
}
BATTERIES_KWH = (0, 10, 15, 20, 25, 30)


def hourly_ote_prices() -> pd.Series:
    raw = pd.read_csv(OUTPUT / "ote_prices_2025.csv")
    index = pd.to_datetime(raw["timestamp"], utc=True)
    series = pd.Series(raw["price_eur_per_mwh"].to_numpy(), index=index).sort_index()
    series = series.resample("h").mean()
    full_index = pd.date_range("2025-01-01T00:00:00Z", "2025-12-31T23:00:00Z", freq="h")
    series = series.reindex(full_index)
    # OTE's chart endpoint contains one incomplete historical day (2025-07-04).
    series = series.interpolate(method="time").ffill().bfill()
    if len(series) != 8760 or series.isna().any():
        raise ValueError("Could not construct a complete 2025 hourly OTE price series")
    return series


def nonflex_house_load() -> tuple[pd.DatetimeIndex, np.ndarray]:
    raw = pd.read_csv(OUTPUT / "alphaess_power_2025.csv", parse_dates=["timestamp"])
    raw = raw.drop_duplicates("timestamp").set_index("timestamp").sort_index()
    # Cap vehicle-charging spikes, then preserve the measured time-of-day and
    # heat-pump shape while reconciling every month to the pre-EV energy totals.
    hourly = raw["load_kw"].clip(lower=0, upper=5.0).resample("h").mean()
    typical = hourly.groupby([hourly.index.month, hourly.index.hour]).median()
    for timestamp in hourly.index[hourly.isna()]:
        hourly.loc[timestamp] = typical.loc[(timestamp.month, timestamp.hour)]
    energy = hourly.to_numpy(copy=True)
    for month in range(1, 13):
        mask = hourly.index.month == month
        energy[mask] *= PRE_EV_MONTHLY_KWH[month - 1] / energy[mask].sum()
    return hourly.index, energy


def pvgis_hourly_per_kwp(aspect: int) -> np.ndarray:
    params = {
        "lat": 49.9681,
        "lon": 14.5135,
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
    return np.array([point["P"] / 1000 for point in payload["outputs"]["hourly"]], dtype=float)


def optimize(
    base_load: np.ndarray,
    pv: np.ndarray,
    spot_price: np.ndarray,
    battery_kwh: float,
    *,
    import_adder_eur_mwh: float,
    export_fee_eur_mwh: float,
    degradation_eur_mwh: float = 45.0,
) -> dict[str, float]:
    n = len(base_load)
    gi, ge, bc, bd, soc, ev, curt = (offset * n for offset in range(7))
    var_count = 7 * n
    charge_eff = 0.95
    discharge_eff = 0.95
    battery_power = min(10.0, max(5.0, battery_kwh / 2)) if battery_kwh else 0.0
    initial_soc = battery_kwh * 0.5

    objective = np.zeros(var_count)
    objective[gi : gi + n] = (spot_price + import_adder_eur_mwh) / 1000
    objective[ge : ge + n] = -(spot_price - export_fee_eur_mwh) / 1000
    objective[bd : bd + n] = degradation_eur_mwh / 1000

    week_slices = [slice(start, min(start + 168, n)) for start in range(0, n, 168)]
    eq_rows = 2 * n + len(week_slices) + 1
    a_eq = lil_matrix((eq_rows, var_count), dtype=float)
    b_eq = np.zeros(eq_rows)

    for t in range(n):
        # Grid + PV + battery discharge = house + EV + battery charge + export + curtailment.
        a_eq[t, gi + t] = 1
        a_eq[t, ge + t] = -1
        a_eq[t, bc + t] = -1
        a_eq[t, bd + t] = 1
        a_eq[t, ev + t] = -1
        a_eq[t, curt + t] = -1
        b_eq[t] = base_load[t] - pv[t]

        row = n + t
        a_eq[row, soc + t] = 1
        if t:
            a_eq[row, soc + t - 1] = -1
            b_eq[row] = 0
        else:
            b_eq[row] = initial_soc
        a_eq[row, bc + t] = -charge_eff
        a_eq[row, bd + t] = 1 / discharge_eff

    annual_days = n / 24
    for index, week in enumerate(week_slices):
        row = 2 * n + index
        a_eq[row, ev + week.start : ev + week.stop] = 1
        days = (week.stop - week.start) / 24
        b_eq[row] = EV_ENERGY_KWH * days / annual_days

    final_row = eq_rows - 1
    a_eq[final_row, soc + n - 1] = 1
    b_eq[final_row] = initial_soc

    bounds = []
    bounds.extend([(0, 20)] * n)  # grid import, matching the confirmed site connection
    bounds.extend([(0, float(max(0, value))) for value in pv])  # PV export only
    bounds.extend([(0, battery_power)] * n)
    bounds.extend([(0, battery_power)] * n)
    bounds.extend([(0, battery_kwh)] * n)
    bounds.extend([(0, 11)] * n)  # combined managed EV charging power
    bounds.extend([(0, float(max(0, value))) for value in pv])

    result = linprog(
        objective,
        A_eq=a_eq.tocsr(),
        b_eq=b_eq,
        bounds=bounds,
        method="highs",
        options={"presolve": True},
    )
    if not result.success:
        raise RuntimeError(result.message)
    x = result.x
    grid_import = x[gi : gi + n]
    grid_export = x[ge : ge + n]
    battery_discharge = x[bd : bd + n]
    ev_charge = x[ev : ev + n]
    curtailment = x[curt : curt + n]
    import_cost = np.sum(grid_import * (spot_price + import_adder_eur_mwh) / 1000)
    export_revenue = np.sum(grid_export * (spot_price - export_fee_eur_mwh) / 1000)
    degradation_cost = np.sum(battery_discharge * degradation_eur_mwh / 1000)
    ev_energy_price = np.sum(ev_charge * (spot_price + import_adder_eur_mwh)) / np.sum(ev_charge)

    return {
        "net_variable_cost_eur": import_cost - export_revenue + degradation_cost,
        "import_cost_eur": import_cost,
        "export_revenue_eur": export_revenue,
        "degradation_cost_eur": degradation_cost,
        "grid_import_kwh": grid_import.sum(),
        "grid_export_kwh": grid_export.sum(),
        "pv_curtailment_kwh": curtailment.sum(),
        "battery_discharge_kwh": battery_discharge.sum(),
        "battery_cycles": battery_discharge.sum() / battery_kwh if battery_kwh else 0,
        "ev_average_import_price_eur_mwh": ev_energy_price,
        "negative_price_import_kwh": grid_import[spot_price < 0].sum(),
        "peak_grid_import_kw": grid_import.max(),
    }


def market_statistics(prices: pd.Series) -> pd.DataFrame:
    local = prices.copy()
    local.index = local.index.tz_convert("Europe/Prague")
    frame = pd.DataFrame({"price": local})
    frame["month"] = frame.index.month
    rows = []
    for month, group in frame.groupby("month"):
        rows.append(
            {
                "month": month,
                "mean_eur_mwh": group.price.mean(),
                "min_eur_mwh": group.price.min(),
                "negative_hours": int((group.price < 0).sum()),
                "hours_at_or_below_20": int((group.price <= 20).sum()),
                "mean_daily_spread_eur_mwh": group.groupby(group.index.date).price.agg(lambda x: x.max() - x.min()).mean(),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    prices = hourly_ote_prices()
    _, base_load = nonflex_house_load()
    north = pvgis_hourly_per_kwp(180)
    south = pvgis_hourly_per_kwp(0)
    price_values = prices.to_numpy()

    stats = market_statistics(prices)
    stats.to_csv(OUTPUT / "ote_2025_monthly_statistics.csv", index=False)

    scenarios = []
    for layout, (north_kwp, south_kwp) in PV_LAYOUTS.items():
        pv = north * north_kwp + south * south_kwp
        for battery_kwh in BATTERIES_KWH:
            result = optimize(
                base_load,
                pv,
                price_values,
                battery_kwh,
                import_adder_eur_mwh=80,
                export_fee_eur_mwh=10,
            )
            result.update(
                {
                    "layout": layout,
                    "north_kwp": north_kwp,
                    "south_kwp": south_kwp,
                    "pv_kwh": pv.sum(),
                    "battery_usable_kwh": battery_kwh,
                    "price_case": "OTE + 80 import adder / OTE - 10 export fee",
                }
            )
            scenarios.append(result)

    frame = pd.DataFrame(scenarios)
    frame.to_csv(OUTPUT / "czech_market_system_scenarios.csv", index=False)
    columns = [
        "layout",
        "battery_usable_kwh",
        "pv_kwh",
        "net_variable_cost_eur",
        "grid_import_kwh",
        "grid_export_kwh",
        "pv_curtailment_kwh",
        "battery_cycles",
        "ev_average_import_price_eur_mwh",
    ]
    print(stats.round(1).to_string(index=False))
    print("\n", frame[columns].round(1).to_string(index=False))


if __name__ == "__main__":
    main()
