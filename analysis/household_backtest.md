> Historical diagnostic: superseded by [the corrected controller validation](controller_v2_validation.md), including the observed charging rate and two charge/two discharge schedules.

# Household AlphaESS backtest

Follow-up: this report evaluates the periodic/timed-executor policies. The household now selects the standard two-window charge-only backend; see [its separate backtest and limitations](standard_daily_backtest.md). The fixed-daily benchmark below is not the standard-API fallback.

Evaluated 20 October 2026. These are modeled counterfactual costs, not measured bill savings or a hardware commissioning test. No inverter commands were issued.

## Decision

Keep the deterministic planner, but do not assume frequent replanning is a large source of value. On 345 usable 2025 days, 15-minute replanning improves modeled economic cost by **€38.74 versus native self-consumption**, **€60.59 versus the previous planner**, and only **€5.55 versus fixed daily decisions**. On the recent 42-day check it beats native by **€19.51**, but costs **€1.10 more than the previous planner**. Parameters were not selected to win either period.

A practical direction is a daily mathematical plan with frequent monitoring and replanning when forecasts or battery state change materially. These results do not justify an LLM making each operating decision. They also do not establish that one daily API write can encode the benchmark: the fixed-daily policy still assumes a quarter-hour executor, and feasibility under the six-period device limit is unverified.

![Modeled benefits](../output/backtest/household_backtest.png)

## Results: current tariff scenario on 2025 household data

345 days, 8 January–31 December 2025, split into 8 continuous segments. All strategies have the same physical battery and segment starting energy. The table excludes fixed monthly charges that operating decisions cannot change.

| Policy | Electricity cash cost | Assumed wear | Energy-adjusted economic cost | Benefit vs native |
|---|---:|---:|---:|---:|
| Native self-consumption | €1,489.34 | €117.80 | €1,610.90 | €+0.00 |
| Previous two-window planner | €1,512.16 | €120.99 | €1,632.75 | €-21.85 |
| Fixed daily decisions | €1,435.39 | €139.30 | €1,577.71 | €+33.19 |
| 15-minute replanning | €1,439.37 | €129.40 | €1,572.16 | €+38.74 |

Economic cost = import cash cost − PV export revenue + assumed battery wear − common-price terminal-energy adjustment. Cash columns include variable fees and VAT. Battery wear is not an invoice charge. Current 2026 tariff coefficients and the assumed 09:00–13:00 VT window were applied to historical conditions; this is not reconstruction of a 2025 bill.

## Recent household API check

Fetched 49 complete days (1 September–19 October 2026), containing 14,140 samples directly from getOneDayPowerBySn. Seven preceding days provide warm-up; 8 September–19 October gives 42 evaluated days. These data were collected after implementation; no policy parameter tuning was performed on them.

| Policy | Electricity cash cost | Assumed wear | Energy-adjusted economic cost | Benefit vs native |
|---|---:|---:|---:|---:|
| Native self-consumption | €217.58 | €6.46 | €224.69 | €+0.00 |
| Previous two-window planner | €189.21 | €14.34 | €204.09 | €+20.61 |
| Fixed daily decisions | €190.14 | €15.08 | €205.87 | €+18.82 |
| 15-minute replanning | €189.71 | €14.82 | €205.18 | €+19.51 |

The rolling policy saves €27.87 in modeled electricity cash cost versus native, but its extra discharge adds €8.36 of assumed wear. Its advantage versus daily decisions is only €0.69 over these 42 days. Do not extrapolate this seasonal period to a year.

## Tariff and wear sensitivity

Historical switching times are not established by the one verified week in October 2026. The following cases rerun decisions on exactly the same recent data; all-NT and all-VT are deliberately simplified extremes, not claimed historical schedules.

| Recent tariff scenario | Rolling benefit vs native | Rolling benefit vs previous planner |
|---|---:|---:|
| Assumed VT 09:00–13:00 | €+19.51 | €-1.10 |
| All NT | €+19.94 | €-2.75 |
| All VT | €+19.12 | €-1.85 |

The recent ranking against the previous planner is not robustly favorable to the new policy under any of these tariff scenarios.

| Wear valuation, unchanged recent actions | Rolling benefit vs native | Rolling benefit vs previous planner |
|---|---:|---:|
| €0.000/discharged kWh | €+27.87 | €-0.61 |
| €0.045/discharged kWh | €+19.51 | €-1.10 |
| €0.090/discharged kWh | €+11.15 | €-1.58 |

This wear table reprices the same actions; it does not retune the policy. Residual stored energy is valued at a prespecified €0.10 per usable kWh at each segment boundary. Initial charge is 50% for every strategy, and energy is carried continuously within each segment; there is no free daily battery refill.

## Information available to each decision

- Load forecasts use the preceding seven completed days, taking the median of source samples in each local quarter hour. No future actual load enters the optimization.
- Solar uses archived ECMWF IFS Single Runs selected with at least six hours between model initialization and decision time. Yesterday’s measured PV calibrates the scale; future measured PV only scores outcomes. GTI values are aligned to the preceding-hour averaging interval.
- Tomorrow’s day-ahead prices become visible at 15:00 Europe/Prague. Earlier decisions cannot use them. Supplier-native fees are converted to EUR using a fixing known at the decision time; scoring uses the historical settlement-day fixing.
- A decision made at a quarter-hour boundary is applied to the next boundary, matching the current controller’s timing. Only the first action is executed before replanning. Actual load and PV then drive the physical simulator.
- Native actions greedily store PV surplus and supply household demand, respecting battery and power limits. Hold, rounded grid-charge targets and minimum power follow the adapter’s intended semantics. No battery-to-grid export or flexible EV charging is modeled.
- Legacy comparisons reuse the actual previous two-window analysis methods. If its optional solar forecast fails, it continues price-only planning, as production did. The common causal forecast differs from the former adaptive 89-day provider.

Archived IFS is a causal weather-forecast variant, not an exact replay of today’s default Open-Meteo best_match model. The historical archive also does not prove the actual vendor delivered each run exactly six hours after initialization. The seven-day persistence alternative gives an additional causal stress case: over 352 evaluable 2025 days, rolling saves €48.73 versus native and €2.61 versus fixed daily decisions. Its dates differ because it does not require archived weather runs.

## Data quality and exclusions

The preexisting household CSV contains 105,186 samples covering all 365 days of 2025. Integration uses elapsed time with piecewise-constant power, extending only short boundary gaps (up to ten minutes). Days with interior gaps over 15 minutes or missing quarter-hour samples are excluded. There is no price interpolation or use of annual/monthly future totals to normalize the load.

Both DST-transition days are excluded because source timestamps omit UTC offsets and cannot reliably identify repeated-hour folds. Three additional measured days are excluded for gaps: 18 February, 5 May and 3 October. OTE prices are absent on 4 July. Seven August days are excluded from the weather evaluation because archived IFS runs required by the decision or calibration are unavailable. Full lists are in the result JSON files. January 1–7 is warm-up.

OTE prices retain their original resolution: hourly prices are held for four quarter hours before October, rather than inventing quarter-hour market variability. The archive has 1,674 valid weather runs; 18 unavailable 2025 model runs are documented in the ignored weather manifest.

Fresh daily API totals were checked on three prespecified dates. Source snapshots are not exact settlement energy, especially when true exports are very small:

| Day | Integrated PV / API daily PV | Integrated import / API daily import | Integrated export / API daily export |
|---|---:|---:|---:|
| 2026-09-01 | 18.80 / 18.80 kWh | 4.77 / 4.44 kWh | 2.80 / 2.54 kWh |
| 2026-09-25 | 12.42 / 12.60 kWh | 22.77 / 23.07 kWh | 0.40 / 0.20 kWh |
| 2026-10-19 | 4.12 / 4.20 kWh | 39.65 / 38.72 kWh | 0.17 / 0.08 kWh |

These integration differences and unmodeled standby/phase effects are material when comparing strategies whose total cost differs by only a few euros. The household load channel is used as reported by AlphaESS. No EV energy is synthesized, removed by clipping spikes, or separately controlled; without a separate circuit channel the historical ESS-suppliable load boundary cannot be independently proven.

## What this verified, and what it did not

All modeled policies had zero reserve violations. There was one import-limit exceedance interval on the 2025 window for every policy, and none in the recent window; this is a household-demand event, not an additional grid-charge overload. Rolling forecast load MAE was 1.09 kW in the annual weather case and 0.83 kW recently. Solar MAE was 0.41 kW and 0.19 kW respectively. There are no simultaneous battery charge/discharge paths or battery exports in the simulator.

The annual weather variant falls back on 3,964 of 33,120 decisions: insufficient PV calibration samples, a missing complete calibration day after a data exclusion, or too short a remaining price horizon. Recent fallback count is 100 of 4032. Fallback metrics are recorded for the rolling controller only; zero in other rows is not proof those policies never used native operation. Robust reuse of a previously valid PV calibration is a useful next improvement, but was not tuned into these results.

Testing exposed and fixed a real one-hour GTI alignment bug in the live provider, including cache-version invalidation and a regression test. Seven backtest tests cover future-data isolation, 15:00 price publication, six-hour weather availability, irradiance semantics, battery energy conservation, reserve/import limits, and ambiguous DST exclusion.

Simulation assumes the inverter successfully implements hold and native self-consumption as modeled. This is not established for the Storion-SMILE-T10. The advanced API still returns 6017, old schedules remain unchanged, device write-frequency permissions are unresolved, phase imbalance and equipment standby consumption are not modeled, and the uncompressed simulated daily plan is not proven to fit six periods. Live automatic control should remain uncommissioned until those facts are verified.

## Reproduce

```bash
uv run python analysis/fetch_backtest_inputs.py --start 2026-09-01 --end 2026-10-19
uv run python analysis/fetch_forecast_archive.py --start 2024-12-31 --end 2025-12-31
uv run python analysis/fetch_forecast_archive.py --start 2026-08-24 --end 2026-10-19
uv run python analysis/backtest_household.py --forecast weather --output analysis/output/backtest_2025_weather
uv run python analysis/backtest_household.py --start 2026-09-08 --end 2026-10-19 --forecast weather --output analysis/output/backtest_2026_weather_schedule
uv run --with matplotlib python analysis/report_backtest.py
```

The archived forecast caches and household files are under ignored data/backtests/. The commands above rerun the principal comparisons; the report also reads the saved all-NT/all-VT, persistence and independently corrected legacy comparisons. To regenerate those, use the same backtest entry point with --tariff nt or vt, --forecast persistence, or --policies legacy and the corresponding saved output prefix. The primary collection details are in [the methodology audit](backtest_methodology.md). Cached archives are required to reproduce the weather variant; do not silently replace missing runs with future actual weather. Result JSON/CSV files are in analysis/output/backtest_*. Source-code and primary-CSV SHA256 hashes, data counts and frozen model assumptions are in [the manifest](output/backtest_manifest.json).

Primary references: [AlphaESS API field/units reference](https://github.com/CharlesGillanders/alphaess-openAPI/blob/main/docs/API.md), [Open-Meteo Single Runs and availability](https://open-meteo.com/en/docs/single-runs-api), [Open-Meteo irradiance definitions](https://open-meteo.com/en/docs), [supplier import terms](https://www.bezdodavatele.cz/wp-content/uploads/2026/01/26_01_EL_Cenik_DOM_Spot_info-1.pdf), [supplier export terms](https://www.bezdodavatele.cz/wp-content/uploads/2026/01/25_10_Podminky-vykupu-mikrozdroje.pdf), [CNB fixing archive](https://www.cnb.cz/en/financial_markets/foreign_exchange_market/exchange_rate_fixing/year_form.html).
