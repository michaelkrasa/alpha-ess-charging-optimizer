"""Assemble audited results and a static comparison figure.

Run: uv run --with matplotlib python analysis/report_backtest.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "analysis/output"
NAMES = {
    "native": "Native self-consumption",
    "legacy": "Previous two-window planner",
    "daily": "Fixed daily decisions",
    "rolling": "15-minute replanning",
}


def corrected(name, baseline):
    """Baseline-only parallel runs use the production optional-solar fallback.

    Replacing only that independent policy is equivalent to running all four
    policies with the final script; identical data windows are checked first.
    """
    path = OUT / f"{name}.json"
    data = json.loads(path.read_text())
    old = json.loads((OUT / f"{baseline}.json").read_text())
    for key in ("start", "end", "days", "segments", "excluded"):
        assert data[key] == old[key], f"Baseline coverage differs: {key}"
    data["totals"]["legacy"] = old["totals"]["legacy"]
    data["baseline_note"] = (
        "Legacy price-only fallback preserved if optional solar forecast is unavailable; baseline recomputed independently with the same data and segment starts."
    )
    path.write_text(json.dumps(data, indent=2, allow_nan=False))
    frame = pd.read_csv(OUT / f"{name}.csv")
    legacy = pd.read_csv(OUT / f"{baseline}.csv")
    frame = pd.concat(
        [frame[frame.policy != "legacy"], legacy], ignore_index=True
    ).sort_values(["day", "policy"])
    frame.to_csv(OUT / f"{name}.csv", index=False)
    return data, frame


def table(data):
    lines = [
        "| Policy | Electricity cash cost | Assumed wear | Energy-adjusted economic cost | Benefit vs native |",
        "|---|---:|---:|---:|---:|",
    ]
    native = data["totals"]["native"]["economic_cost_eur"]
    for key in ("native", "legacy", "daily", "rolling"):
        t = data["totals"][key]
        lines.append(
            f"| {NAMES[key]} | €{t['cash_eur']:,.2f} | €{t['wear_eur']:,.2f} | €{t['economic_cost_eur']:,.2f} | €{native - t['economic_cost_eur']:+.2f} |"
        )
    return "\n".join(lines)


def plot(annual, recent, destination):
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.2), gridspec_kw={"wspace": 0.4})
    fig.patch.set_facecolor("#f6f5f0")
    for ax, data, title in zip(
        axes,
        [annual, recent],
        ["2025 · 345 evaluated days", "8 Sep–19 Oct 2026 · 42 days"],
    ):
        ax.set_facecolor("#f6f5f0")
        native = data["totals"]["native"]["economic_cost_eur"]
        values = [
            native - data["totals"][key]["economic_cost_eur"]
            for key in ("legacy", "daily", "rolling")
        ]
        bars = ax.barh(
            [2, 1, 0], values, color=["#a66a52", "#9ba88d", "#41674b"], height=0.48
        )
        ax.set_yticks(
            [2, 1, 0], ["Previous planner", "Daily decisions", "15-minute decisions"]
        )
        ax.axvline(0, color="#9ca394", linewidth=0.8)
        ax.set_title(title, loc="left", fontweight="bold", pad=20)
        ax.set_xlabel("Modeled benefit vs native self-consumption (€)")
        left = min(-5, min(values) - 12)
        right = max(values) + 15
        ax.set_xlim(left, right)
        for bar, value in zip(bars, values):
            ax.text(
                value + 1 if value >= 0 else value / 2,
                bar.get_y() + bar.get_height() / 2,
                f"€{value:+.2f}",
                va="center",
                ha="left" if value >= 0 else "center",
                color="#202020" if value >= 0 else "white",
                fontweight="bold",
            )
        ax.grid(axis="x", alpha=0.15)
        ax.set_axisbelow(True)
    fig.suptitle(
        "Replanning helps a little. It does not win every comparison.",
        x=0.07,
        ha="left",
        fontsize=17,
        fontweight="bold",
        y=0.98,
    )
    fig.text(
        0.07,
        0.89,
        "Household telemetry + causal archived forecasts · battery wear and ending energy included",
        fontsize=10,
        color="#60675e",
    )
    fig.text(
        0.07,
        0.04,
        "Simulation, not measured bill savings. Current tariff applied to historical data; past VT/NT times assumed.\nSuccessful device control remains unverified. Fixed daily decisions still require a timed executor.",
        fontsize=9,
        color="#60675e",
    )
    fig.subplots_adjust(left=0.15, right=0.98, top=0.77, bottom=0.22)
    fig.savefig(destination, dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    annual, annual_daily = corrected(
        "backtest_2025_weather", "backtest_2025_weather_schedule_legacy"
    )
    recent, recent_daily = corrected(
        "backtest_2026_weather_schedule", "backtest_2026_weather_schedule_legacy"
    )
    sensitivity = {}
    for mode in ("nt", "vt"):
        sensitivity[mode] = corrected(
            f"backtest_2026_weather_{mode}", f"backtest_2026_weather_{mode}_legacy"
        )[0]
    persistence = json.loads((OUT / "backtest_2025_persistence.json").read_text())
    target = ROOT / "output/backtest"
    target.mkdir(parents=True, exist_ok=True)
    plot(annual, recent, target / "household_backtest.png")
    raw = list((ROOT / "data/backtests/household").glob("*.json"))
    samples = sum(len(json.loads(p.read_text())["samples"]) for p in raw)
    weather = json.loads(
        (ROOT / "data/backtests/weather/fetch_manifest.json").read_text()
    )
    # No source identifiers are copied into the result manifest.
    manifest = {
        "method": "Causal walk-forward, device-action simulation",
        "current_tariff_scenario": True,
        "nominal_capacity_kwh": 17.2,
        "reserve_percent": 10,
        "charge_kw": 5,
        "discharge_kw": 5,
        "one_way_efficiency": 0.95,
        "wear_eur_per_discharge_kwh": 0.045,
        "terminal_eur_per_usable_kwh": 0.10,
        "assumed_historical_vt": ["09:00", "13:00"],
        "new_api_days": len(raw),
        "new_api_samples": samples,
        "annual_days": annual["days"],
        "recent_days": recent["days"],
        "hashes": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                ROOT / "src/manager/planner.py",
                ROOT / "src/manager/providers.py",
                ROOT / "manager.yaml",
                ROOT / "analysis/backtest_household.py",
                ROOT / "analysis/output/alphaess_power_2025.csv",
                ROOT / "analysis/output/ote_prices_2025.csv",
            ]
        },
    }
    (OUT / "backtest_manifest.json").write_text(json.dumps(manifest, indent=2))
    a = annual["totals"]
    r = recent["totals"]
    lines = [
        "# Household AlphaESS backtest",
        "",
        "Follow-up: this report evaluates the periodic/timed-executor policies. The household now selects the standard two-window charge-only backend; see [its separate backtest and limitations](standard_daily_backtest.md). The fixed-daily benchmark below is not the standard-API fallback.",
        "",
        "Evaluated 20 October 2026. These are modeled counterfactual costs, not measured bill savings or a hardware commissioning test. No inverter commands were issued.",
        "",
        "## Decision",
        "",
        f"Keep the deterministic planner, but do not assume frequent replanning is a large source of value. On {annual['days']} usable 2025 days, 15-minute replanning improves modeled economic cost by **€{a['native']['economic_cost_eur'] - a['rolling']['economic_cost_eur']:.2f} versus native self-consumption**, **€{a['legacy']['economic_cost_eur'] - a['rolling']['economic_cost_eur']:.2f} versus the previous planner**, and only **€{a['daily']['economic_cost_eur'] - a['rolling']['economic_cost_eur']:.2f} versus fixed daily decisions**. On the recent 42-day check it beats native by **€{r['native']['economic_cost_eur'] - r['rolling']['economic_cost_eur']:.2f}**, but costs **€{r['rolling']['economic_cost_eur'] - r['legacy']['economic_cost_eur']:.2f} more than the previous planner**. Parameters were not selected to win either period.",
        "",
        "A practical direction is a daily mathematical plan with frequent monitoring and replanning when forecasts or battery state change materially. These results do not justify an LLM making each operating decision. They also do not establish that one daily API write can encode the benchmark: the fixed-daily policy still assumes a quarter-hour executor, and feasibility under the six-period device limit is unverified.",
        "",
        "![Modeled benefits](../output/backtest/household_backtest.png)",
        "",
        "## Results: current tariff scenario on 2025 household data",
        "",
        f"{annual['days']} days, 8 January–31 December 2025, split into {annual['segments']} continuous segments. All strategies have the same physical battery and segment starting energy. The table excludes fixed monthly charges that operating decisions cannot change.",
        "",
        table(annual),
        "",
        "Economic cost = import cash cost − PV export revenue + assumed battery wear − common-price terminal-energy adjustment. Cash columns include variable fees and VAT. Battery wear is not an invoice charge. Current 2026 tariff coefficients and the assumed 09:00–13:00 VT window were applied to historical conditions; this is not reconstruction of a 2025 bill.",
        "",
        "## Recent household API check",
        "",
        f"Fetched {len(raw)} complete days (1 September–19 October 2026), containing {samples:,} samples directly from getOneDayPowerBySn. Seven preceding days provide warm-up; 8 September–19 October gives 42 evaluated days. These data were collected after implementation; no policy parameter tuning was performed on them.",
        "",
        table(recent),
        "",
        f"The rolling policy saves €{r['native']['cash_eur'] - r['rolling']['cash_eur']:.2f} in modeled electricity cash cost versus native, but its extra discharge adds €{r['rolling']['wear_eur'] - r['native']['wear_eur']:.2f} of assumed wear. Its advantage versus daily decisions is only €{r['daily']['economic_cost_eur'] - r['rolling']['economic_cost_eur']:.2f} over these 42 days. Do not extrapolate this seasonal period to a year.",
        "",
        "## Tariff and wear sensitivity",
        "",
        "Historical switching times are not established by the one verified week in October 2026. The following cases rerun decisions on exactly the same recent data; all-NT and all-VT are deliberately simplified extremes, not claimed historical schedules.",
        "",
        "| Recent tariff scenario | Rolling benefit vs native | Rolling benefit vs previous planner |",
        "|---|---:|---:|",
    ]
    for name, data in [
        ("Assumed VT 09:00–13:00", recent),
        ("All NT", sensitivity["nt"]),
        ("All VT", sensitivity["vt"]),
    ]:
        t = data["totals"]
        lines.append(
            f"| {name} | €{t['native']['economic_cost_eur'] - t['rolling']['economic_cost_eur']:+.2f} | €{t['legacy']['economic_cost_eur'] - t['rolling']['economic_cost_eur']:+.2f} |"
        )
    lines += [
        "",
        "The recent ranking against the previous planner is not robustly favorable to the new policy under any of these tariff scenarios.",
        "",
        "| Wear valuation, unchanged recent actions | Rolling benefit vs native | Rolling benefit vs previous planner |",
        "|---|---:|---:|",
    ]
    for wear in (0, 0.045, 0.09):
        cost = lambda t: (
            t["cash_eur"] + wear * t["battery_discharge_kwh"] - t["terminal_credit_eur"]
        )
        lines.append(
            f"| €{wear:.3f}/discharged kWh | €{cost(r['native']) - cost(r['rolling']):+.2f} | €{cost(r['legacy']) - cost(r['rolling']):+.2f} |"
        )
    lines += [
        "",
        "This wear table reprices the same actions; it does not retune the policy. Residual stored energy is valued at a prespecified €0.10 per usable kWh at each segment boundary. Initial charge is 50% for every strategy, and energy is carried continuously within each segment; there is no free daily battery refill.",
        "",
        "## Information available to each decision",
        "",
        "- Load forecasts use the preceding seven completed days, taking the median of source samples in each local quarter hour. No future actual load enters the optimization.",
        "- Solar uses archived ECMWF IFS Single Runs selected with at least six hours between model initialization and decision time. Yesterday’s measured PV calibrates the scale; future measured PV only scores outcomes. GTI values are aligned to the preceding-hour averaging interval.",
        "- Tomorrow’s day-ahead prices become visible at 15:00 Europe/Prague. Earlier decisions cannot use them. Supplier-native fees are converted to EUR using a fixing known at the decision time; scoring uses the historical settlement-day fixing.",
        "- A decision made at a quarter-hour boundary is applied to the next boundary, matching the current controller’s timing. Only the first action is executed before replanning. Actual load and PV then drive the physical simulator.",
        "- Native actions greedily store PV surplus and supply household demand, respecting battery and power limits. Hold, rounded grid-charge targets and minimum power follow the adapter’s intended semantics. No battery-to-grid export or flexible EV charging is modeled.",
        "- Legacy comparisons reuse the actual previous two-window analysis methods. If its optional solar forecast fails, it continues price-only planning, as production did. The common causal forecast differs from the former adaptive 89-day provider.",
        "",
        "Archived IFS is a causal weather-forecast variant, not an exact replay of today’s default Open-Meteo best_match model. The historical archive also does not prove the actual vendor delivered each run exactly six hours after initialization. The seven-day persistence alternative gives an additional causal stress case: over 352 evaluable 2025 days, rolling saves €"
        + f"{persistence['totals']['native']['economic_cost_eur'] - persistence['totals']['rolling']['economic_cost_eur']:.2f}"
        + " versus native and €"
        + f"{persistence['totals']['daily']['economic_cost_eur'] - persistence['totals']['rolling']['economic_cost_eur']:.2f}"
        + " versus fixed daily decisions. Its dates differ because it does not require archived weather runs.",
        "",
        "## Data quality and exclusions",
        "",
        "The preexisting household CSV contains 105,186 samples covering all 365 days of 2025. Integration uses elapsed time with piecewise-constant power, extending only short boundary gaps (up to ten minutes). Days with interior gaps over 15 minutes or missing quarter-hour samples are excluded. There is no price interpolation or use of annual/monthly future totals to normalize the load.",
        "",
        "Both DST-transition days are excluded because source timestamps omit UTC offsets and cannot reliably identify repeated-hour folds. Three additional measured days are excluded for gaps: 18 February, 5 May and 3 October. OTE prices are absent on 4 July. Seven August days are excluded from the weather evaluation because archived IFS runs required by the decision or calibration are unavailable. Full lists are in the result JSON files. January 1–7 is warm-up.",
        "",
        "OTE prices retain their original resolution: hourly prices are held for four quarter hours before October, rather than inventing quarter-hour market variability. The archive has 1,674 valid weather runs; 18 unavailable 2025 model runs are documented in the ignored weather manifest.",
        "",
        "Fresh daily API totals were checked on three prespecified dates. Source snapshots are not exact settlement energy, especially when true exports are very small:",
        "",
        "| Day | Integrated PV / API daily PV | Integrated import / API daily import | Integrated export / API daily export |",
        "|---|---:|---:|---:|",
    ]
    from analysis.backtest_household import load_history

    days, _ = load_history()
    checks = json.loads((ROOT / "data/backtests/daily_checks.json").read_text())
    from datetime import date

    for day, api in checks.items():
        d = days[date.fromisoformat(day)]
        lines.append(
            f"| {day} | {sum(d.solar) * 0.25:.2f} / {api['epv']:.2f} kWh | {sum(d.imports) * 0.25:.2f} / {api['eInput']:.2f} kWh | {sum(d.exports) * 0.25:.2f} / {api['eOutput']:.2f} kWh |"
        )
    lines += [
        "",
        "These integration differences and unmodeled standby/phase effects are material when comparing strategies whose total cost differs by only a few euros. The household load channel is used as reported by AlphaESS. No EV energy is synthesized, removed by clipping spikes, or separately controlled; without a separate circuit channel the historical ESS-suppliable load boundary cannot be independently proven.",
        "",
        "## What this verified, and what it did not",
        "",
        f"All modeled policies had zero reserve violations. There was one import-limit exceedance interval on the 2025 window for every policy, and none in the recent window; this is a household-demand event, not an additional grid-charge overload. Rolling forecast load MAE was {annual['forecast_load_mae_kw']:.2f} kW in the annual weather case and {recent['forecast_load_mae_kw']:.2f} kW recently. Solar MAE was {annual['forecast_solar_mae_kw']:.2f} kW and {recent['forecast_solar_mae_kw']:.2f} kW respectively. There are no simultaneous battery charge/discharge paths or battery exports in the simulator.",
        "",
        f"The annual weather variant falls back on {annual['totals']['rolling']['fallback_intervals']:,} of {annual['days'] * 96:,} decisions: insufficient PV calibration samples, a missing complete calibration day after a data exclusion, or too short a remaining price horizon. Recent fallback count is {recent['totals']['rolling']['fallback_intervals']} of {recent['days'] * 96}. Fallback metrics are recorded for the rolling controller only; zero in other rows is not proof those policies never used native operation. Robust reuse of a previously valid PV calibration is a useful next improvement, but was not tuned into these results.",
        "",
        "Testing exposed and fixed a real one-hour GTI alignment bug in the live provider, including cache-version invalidation and a regression test. Seven backtest tests cover future-data isolation, 15:00 price publication, six-hour weather availability, irradiance semantics, battery energy conservation, reserve/import limits, and ambiguous DST exclusion.",
        "",
        "Simulation assumes the inverter successfully implements hold and native self-consumption as modeled. This is not established for the Storion-SMILE-T10. The advanced API still returns 6017, old schedules remain unchanged, device write-frequency permissions are unresolved, phase imbalance and equipment standby consumption are not modeled, and the uncompressed simulated daily plan is not proven to fit six periods. Live automatic control should remain uncommissioned until those facts are verified.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "uv run python analysis/fetch_backtest_inputs.py --start 2026-09-01 --end 2026-10-19",
        "uv run python analysis/fetch_forecast_archive.py --start 2024-12-31 --end 2025-12-31",
        "uv run python analysis/fetch_forecast_archive.py --start 2026-08-24 --end 2026-10-19",
        "uv run python analysis/backtest_household.py --forecast weather --output analysis/output/backtest_2025_weather",
        "uv run python analysis/backtest_household.py --start 2026-09-08 --end 2026-10-19 --forecast weather --output analysis/output/backtest_2026_weather_schedule",
        "uv run --with matplotlib python analysis/report_backtest.py",
        "```",
        "",
        "The archived forecast caches and household files are under ignored data/backtests/. The commands above rerun the principal comparisons; the report also reads the saved all-NT/all-VT, persistence and independently corrected legacy comparisons. To regenerate those, use the same backtest entry point with --tariff nt or vt, --forecast persistence, or --policies legacy and the corresponding saved output prefix. The primary collection details are in [the methodology audit](backtest_methodology.md). Cached archives are required to reproduce the weather variant; do not silently replace missing runs with future actual weather. Result JSON/CSV files are in analysis/output/backtest_*. Source-code and primary-CSV SHA256 hashes, data counts and frozen model assumptions are in [the manifest](output/backtest_manifest.json).",
        "",
        "Primary references: [AlphaESS API field/units reference](https://github.com/CharlesGillanders/alphaess-openAPI/blob/main/docs/API.md), [Open-Meteo Single Runs and availability](https://open-meteo.com/en/docs/single-runs-api), [Open-Meteo irradiance definitions](https://open-meteo.com/en/docs), [supplier import terms](https://www.bezdodavatele.cz/wp-content/uploads/2026/01/26_01_EL_Cenik_DOM_Spot_info-1.pdf), [supplier export terms](https://www.bezdodavatele.cz/wp-content/uploads/2026/01/25_10_Podminky-vykupu-mikrozdroje.pdf), [CNB fixing archive](https://www.cnb.cz/en/financial_markets/foreign_exchange_market/exchange_rate_fixing/year_form.html).",
        "",
    ]
    (ROOT / "analysis/household_backtest.md").write_text("\n".join(lines))
    print("Report:", ROOT / "analysis/household_backtest.md")
    print("Figure:", target / "household_backtest.png")
    print(
        "Annual rolling benefit vs native:",
        a["native"]["economic_cost_eur"] - a["rolling"]["economic_cost_eur"],
    )
    print(
        "Recent rolling benefit vs legacy:",
        r["legacy"]["economic_cost_eur"] - r["rolling"]["economic_cost_eur"],
    )


if __name__ == "__main__":
    main()
