# Rolling control, forecasting and next steps — 21 October 2026

> Superseded: these replays used the removed 20 kW planning constraint and incorrectly allowed the battery to supply EV charging. The owner clarified that EV charging prevents battery discharge entirely. The corrected EMHASS comparison retains the full EV grid bill, separates likely EV demand from forecasting and replays the interlock at original meter resolution. These earlier figures cannot validate current household savings.

The rolling controller improves on the same planner run once per day in both tested periods. Its advantage over the original household controller is tiny in the recent period, and it costs more on the cash electricity bill there. We have not demonstrated a compelling or robust financial advantage over the original setup. More frequent updates alone are insufficient.

## Comparable household results

All figures are EUR; lower is better. Economic cost = electricity import minus surplus-PV export revenue + modeled wear − ending-energy adjustment. Capital costs and fixed standing charges are excluded.

| Strategy | Recent 42 days: cash | Recent: economic | July 18 days: cash | July: economic |
| --- | ---: | ---: | ---: | ---: |
| Native self-consumption | 217.91 | 224.85 | 16.31 | 26.22 |
| Original midnight controller | 191.74 | 205.79 | 27.18 | 30.98 |
| Same current planner, once daily at 15:00 | 196.48 | 207.92 | 16.19 | 25.23 |
| Current rolling controller, every 15 minutes | 194.27 | 205.33 | 14.90 | 24.24 |

Recent rolling improvement over the same current planner at one decision per day is €2.59 economically, or €2.22 in cash. Against the original controller it is only €0.46 economically; the cash bill is **€2.53 higher**. That narrow economic win depends on the wear assumption and the valuation of ending energy. July rolling improvement is €0.99 over the same once-daily planner and €6.74 over the original, economically. Neither short period should be annualized.

Recent equivalent usable battery cycles: original 24.59, once-daily current planner 18.95, rolling 18.30. Rolling stores 145.88 kWh of surplus PV versus original 99.65 kWh and once-daily 148.01 kWh: it captures more PV than the original, but this is not uniform superiority over the same planner run daily. Native captures 151.47 kWh. PV capture alone does not determine the bill.

All strategies preserve the 10% reserve in this simulation. Recent quarter-hour import averages exceed the configured 20 kW planning threshold in five rolling intervals (peak 21.10 kW), one once-daily interval (20.47 kW), and zero original intervals (19.81 kW). July has none. The forecast envelope is not a physical import limiter. These figures do not establish per-phase or instantaneous peaks.

## What the running controller actually uses

- **Measured SOC:** fetched and used as the starting battery state every quarter hour. Both actual installed configurations are read back and remain candidate schedules.
- **Demand:** a mean of integrated quarter-hour demand from at least six of the previous seven completed days. The same clock-time profile is used for today and tomorrow. It has no weekday/temperature model and no intraday correction from recent measured demand. The configured 365-day history is not a trained year-long forecasting model.
- **Solar:** Open-Meteo tilted irradiance for the configured location/orientation, refreshed hourly, calibrated against actual PV production with a 0.8 safety factor. A valid previous calibration can survive cloudy days for at most seven days. It affects expected net demand, available battery headroom and charging economics. It is not a detailed trained PV model.
- **Peaks:** charging candidates are checked against the largest prior-week load around each quarter hour, within ±30 minutes. This conservative envelope rejects risky forecast windows; it does not predict unexpected household peaks or actively clamp actual grid import.
- **Prices and costs:** published spot EUR prices, import fees/VAT, export fees, 95% efficiency each direction, estimated wear of €0.045/discharged kWh. Battery export is excluded. The current price source is OTE; ENTSO-E remains an option.

The causal next-quarter forecast audit covers 4,032 recent intervals. Demand MAE is **1.16 kW**, PV MAE **0.19 kW**, net-demand MAE **1.23 kW**. For the highest-demand 10% of intervals, the demand profile underpredicts by **5.04 kW** on average. Near-zero total demand bias hides poor peak prediction. These retrospective error categories are diagnostics; actual future peaks are never passed into forecasts. See `output/forecast_diagnostics.json` and `evaluate_forecasts.py`.

## Why frequent updates do not guarantee a better bill

The old daily schedule already captures many large price spreads. Extra flexibility has financial value when it corrects errors or exploits opportunities the daily schedule missed. It cannot change physical battery conversion efficiency. Forecast uncertainty, fees, degradation and the inability to sell battery energy limit arbitrage. Filling at night can be profitable, but it can also displace cheap solar the next day.

Our current schedule search scores a bounded set of window candidates; it is not a globally optimal mathematical solver for every possible hardware schedule. Its objective also uses an approximate ending-energy value over a changing published-price horizon. Knowing prices does not make demand or solar known. A revised schedule can look cheaper to this model and cost more in reality. The installed schedule being an available candidate does not establish realized-cost dominance.

## Experiment and validation boundaries

The new `standard_rolling` replay uses the actual standard-API planner with `minimum_slots=4` and the installed pair as a candidate, updated every quarter from that policy's simulated SOC. It is distinct from the older policy named `rolling`, which models an ideal periodic executor and must not be cited as evidence for this household's standard API.

`standard_once_daily` uses the same planner contract and installed-pair candidate, but makes exactly one decision attempt per day at 15:00, including on unchanged-schedule days. This isolates cadence more closely than the historical `standard_daily` policy, which retained a daily write budget and could retry planning. The original midnight algorithm is a separate reference. All share the same physical simulator, causal forecasts and tariffs. Forecast calibration fallback was corrected in the replay for all strategies; hence the recent original result differs from older reports. The first-pass `validated_rolling_recent` output predates that correction and is superseded by `validated_rolling_recent_v2`.

Actual future demand/PV only drive simulated outcomes. Demand forecasts use completed past days; archived weather has a six-hour publication lag; tomorrow's prices are hidden until 15:00 local. The historical irradiance calibration uses archived forecasts, not an exact reconstruction of Open-Meteo's live past-weather product. Central battery capacity is 14.2 kWh, with planning scored across 14.2/15.48/17.2 kWh. Charging is fixed at 8.6 kW, without fictitious dynamic import clipping.

Sep 8–Oct 19 contains 42 continuous usable days; July contains 18 usable days in three segments, with gaps and warm-up exclusions recorded. Each segment begins at common 50% SOC with no managed schedule; daily control waits until its first scheduled decision while rolling starts immediately. State carries within segments. Ending usable energy receives a prespecified €0.10/kWh adjustment. Startup and terminal assumptions can matter to small differences. These are reused research periods, not a newly untouched holdout, and this run does not establish annual savings or robustness to all physical assumptions.

The replay assumes atomic next-quarter schedule application. Real paired endpoint writes are sequential and take time, can fail, and require confirmation. It does not reproduce write jitter, outages, temperature, firmware behavior or phase limits. Rolling forecast failures restore native after the live grace period; short missing-price horizons at data boundaries are recorded rather than filled with future prices.

Production settings and the live controller were unchanged during this evaluation. Analysis-only changes add the two cadence policies, diagnostics, bounded cloudy-calibration reuse and failure fallback. **519 tests pass, 131 skip**, with one existing Starlette deprecation warning. New tests verify updated SOC/installed pairs, exactly one daily decision on unchanged days, non-renewing calibration expiry and native fallback after the grace period. Static checks and whitespace checks pass.

## Recommended direction

Use a deterministic rolling optimizer with a stronger household forecast. First compare same-weekday demand and a short-lived correction from recent consumption against the weekly mean. Then test a small skforecast/LightGBM model with lagged demand, rolling statistics, calendar and archived forecast temperature. Train only on prior data and use chronological walk-forward validation. Quantile forecasts can represent uncertainty, but their coverage must be measured. Select a model on downstream EUR cost, cycles and constraint violations, not MAE alone. There is no need for an LLM making quarter-hour hardware decisions or a large custom neural model.

[EMHASS](https://emhass.readthedocs.io/en/latest/study_cases/mpc.html) is a strong Python MPC/forecasting reference. [Predbat](https://github.com/springfall2008/batpred/blob/b743b5025cfbfaaccfb2b5c8f5ca52136f8f0ce2/docs/components.md) now includes native AlphaESS and can run without Home Assistant, but calls that integration experimental. Benchmark these before deciding to replace our numerical core; preserve the tested actuator and dashboard where useful. Generic power schedules must respect our fixed-power charge and permission-only discharge. See [primary-source research](open_source_forecasting_research.md) for verified capabilities and caveats.

Require a meaningful gain across chronological evaluation periods, sensitivity to wear/capacity/terminal values, and shadow operation before calling the replacement financially proven. The present results justify improving the forecasting and optimization, not claiming the exercise is finished or that faster polling guarantees savings.

## Reproduction

```sh
uv run python analysis/backtest_household.py --start 2026-09-08 --end 2026-10-19 --forecast weather --policies native legacy standard_once_daily standard_rolling --output analysis/output/reproduced_rolling_recent
uv run python analysis/backtest_household.py --start 2026-07-01 --end 2026-07-31 --forecast weather --policies native legacy standard_once_daily standard_rolling --output analysis/output/reproduced_rolling_july
uv run python analysis/evaluate_forecasts.py --start 2026-09-08 --end 2026-10-19
```

Machine-readable source results are `validated_rolling_recent_v2`, `validated_rolling_july`, `validated_once_daily_recent`, `validated_once_daily_july` JSON/CSV pairs under `output/`. The combined table is `rolling_validation_comparison.json`. Replays may be combined as in the commands above; shared forecasting is causal and policy-independent.
