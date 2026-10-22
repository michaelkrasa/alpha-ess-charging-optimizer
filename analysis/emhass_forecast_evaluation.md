# Actual EMHASS forecast and battery-identification evaluation

21 October 2026. Offline work only; no inverter commands or production parameter changes.

## Findings

The package's tools were exercised on this household's measurements, not merely recommended from documentation. There is **no consistent forecast winner across the tested seasons**. A small EMHASS Ridge autoregressive model helps on the recent period and short intraday test, but it is worse than the seven-day mean in autumn 2025. The package's GPS-aware learned PV correction also improves the recent period while worsening autumn 2025. These are forecast-quality findings; selecting the live optimizer still requires the separate shared-physics financial replay.

### Household demand, daily issued forecasts

All values below are watts of mean absolute error (MAE); lower is better. Methods within each column use exactly the same observed, non-EV-inferred target intervals. Forecasts are issued at **00:00 Europe/Prague**, using strictly earlier measurements, and contain 192 quarter-hour steps. The first 96 steps are scored to avoid counting overlapping future targets twice.

| Method | Oct–Dec 2025 | 8 Sep–19 Oct 2026 |
|---|---:|---:|
| Previous seven-day mean | 783.59 | 438.71 |
| Same weekday one week earlier | 1,018.55 | 549.07 |
| Seven-day mean with EMHASS first-step measurement blend | **782.97** | 439.44 |
| EMHASS Ridge, 96 lags plus calendar features | 965.65 | **411.25** |
| Scored intervals | 5,382 | 3,848 |

The blend only adjusts the first forecast quarter, so its effect on full-day MAE is understandably tiny. Recent Ridge MAE improves by 6.3%, but its bias is −115.85 W versus −5.22 W for the mean. In the highest-demand decile, Ridge underestimates by 1,861.65 W on average, versus 1,589.53 W for the mean. Thus better overall MAE does not imply better peak anticipation or lower electricity costs.

A descriptive paired seven-day block bootstrap places the Ridge-minus-mean MAE difference at **−27.46 W, 95% interval [−61.55, +5.50] W** for the recent period, and **+182.06 W [25.71, 364.29] W** for autumn 2025. The recent overall MAE advantage is uncertain; the older deterioration is material. The bootstrap resamples available scored origins and is not a claim of independent measurements or a new model-selection test. Results: [daily metrics, 2025](output/emhass_forecasting_2025_autumn.json), [daily metrics, 2026](output/emhass_forecasting_2026_recent.json), [paired uncertainty](output/emhass_forecast_paired_uncertainty.json).

### Actual quarter-hour context refresh

The frozen model was fitted before 12 October 2026 and then given a newly reconstructed, strictly historical autoregressive window at each quarter-hour through 18 October. This produced **672 separate 48-hour forecast paths**. On 644 non-inferred first-step targets:

| Next-quarter prediction | MAE W | RMSE W |
|---|---:|---:|
| Keep the midnight seven-day-mean forecast | 478.33 | 813.35 |
| Keep the midnight Ridge forecast | 393.87 | 746.14 |
| Predict the latest measured demand persists | 271.99 | 491.68 |
| Refresh EMHASS Ridge's observed last window every quarter | **261.12** | **476.23** |

Measurement feedback clearly helps in this week. The learned model's incremental improvement over simple last-measurement persistence is only about 4%. This is a **seven-day one-step forecast comparison**, not an annual financial result and not evidence that all future leads improve equally. Metrics and train cutoff: [intraday results](output/emhass_forecasting_intraday.json).

### Solar: actual released EMHASS regression

This ran `Forecast.adjust_pv_forecast_data_prep`, `adjust_pv_forecast_fit` and `adjust_pv_forecast_predict`, with the package's default Lasso regression, 90 days of earlier data, a weekly refit and five-fold time-series cross-validation. Features include the baseline issued PV forecast, GPS-derived solar elevation and azimuth, cyclic time of day, and calendar variables. The comparison baseline calibrates archived global tilted irradiance (GTI) against the previous completed day's measured PV, applies the existing 0.8 factor, and permits a seven-day fallback for cloudy calibration days.

| Solar method | Oct–Dec 2025 MAE W | Recent 42 days MAE W | Recent RMSE W |
|---|---:|---:|---:|
| Causal GTI calibration baseline | **87.91** | 187.95 | 395.00 |
| EMHASS learned GPS/calendar correction | 102.98 | **175.23** | **342.66** |
| Scored quarters | 8,640 | 4,032 | 4,032 |

The learned correction removes much of the recent underprediction bias: −107.40 W becomes +13.78 W. In autumn 2025 it instead changes −36.21 W into +47.68 W and worsens MAE. No holdout-specific switch or new seasonal tuning was selected after these results. Metrics: [solar 2025](output/emhass_pv_2025_autumn.json), [solar 2026](output/emhass_pv_2026_recent.json).

The weather inputs are archived ECMWF IFS forecast runs selected using a **six-hour publication lag**. Hourly GTI is aligned as a preceding-hour average, not shifted to grant future knowledge. GPS comes from the household's existing configuration. The available historical weather cache contains GTI, **not archived temperature**, so this experiment does not pretend to have evaluated a temperature-aware load model. No additional office-system generation feed was identified or substituted for this household's PV. Current live weather must not be used to rewrite historical forecasts.

### Battery identification

The released pure `BatteryIdentification.identify` estimator was run independently on five-minute signed AC battery power and reported SOC. Both periods passed the package's statistical confidence and sanity checks:

| Period | Estimated capacity kWh | Bootstrap interval kWh | Estimated RTE | Symmetric one-way efficiency |
|---|---:|---:|---:|---:|
| 2025 | 14.3513 | 14.2572–14.4387 | 0.9611 | 0.9804 |
| Jun–Oct 2026 | 14.1682 | 14.0504–14.2831 | 0.9704 | 0.9851 |

These support keeping approximately 14.2 kWh in the physical-capacity sensitivity set. They are **SOC-coordinate estimates**, with AC battery power derived from site energy balance; they are not independent laboratory capacity or revenue-meter loss measurements. Separate charge and discharge efficiencies are unidentifiable from this input, and the symmetric split is the package's explicit convention. Main replay should retain its declared 0.95 one-way assumptions and also show sensitivity near the estimated 0.98–0.985. No live physics setting was changed. Full confidence and crosschecks: [identification output](output/emhass_battery_identification.json).

## Training, EV separation and causality

The input export contains 473 complete quarter-hour days spanning 2025 and June–October 2026. Dedicated AlphaESS charger fields are disconnected/zero; the EV separation is an **inference**, based on a sustained load step without battery discharge and with SOC not falling. Concurrent battery charging is allowed. The inferred EV load remains in the site's electricity bill, but cannot be supplied by the simulated battery. Household-demand values inside inferred EV sessions are baseline estimates, so **all flagged target quarters are excluded from accuracy scoring**. Those estimates remain uncertain training/context inputs.

The compared model family and hyperparameters were selected/frozen before held-out results: 96 contiguous demand lags, package calendar features, 90-day maximum history, weekly refits, nonnegative forecasts, and a 48-hour package fit/validation split. Ridge was selected over the package's default GradientBoosting model on 15–28 June 2025 development data (MAE 1,084.97 versus 1,307.28 W under the then-stricter EV label definition). After correcting EV semantics to permit concurrent battery charging, the selected model and hyperparameters were retained; there was no held-out model reselection. The frozen spec records both the semantic correction and data hashes. [Frozen configuration](output/emhass_forecasting_frozen_spec.json).

A completed-episode EV label can use samples from its ending. To prevent that from leaking into a forecast issued during an ongoing session, each forecast reconstructs the **newest 11 hours** of its demand history by running the detector on source samples strictly **before** that forecast origin, with extra earlier context. The reconstructed samples are integrated to quarters using their actual durations. If recording gaps, too few samples or a truncated recent window prevent that reconstruction, recent inferred labels are replaced with **raw observed site power**, and quarters without full observation remain NaN; the method never falls back to the retrospectively EV-cleaned values. Older labels are completed because the detector's maximum session duration is ten hours. Fitting excludes the final 48 hours; prediction then supplies the available observed last window. This does not turn the inferred EV power into independently metered ground truth.

Strict-idle EV results and intermediate retrospective-label results remain separate sensitivity artifacts under `data/emhass/forecasts_strict_ev_sensitivity`, `forecasts_retrospective_ev_sensitivity`, and corresponding output JSON suffixes. The primary files above use the corrected non-discharging EV criterion and causal context reconstruction. Intermediate results affected by an integration timestamp-unit bug were corrected and regenerated before acceptance; primary caches pass explicit time-grid, finite-value and positive-energy checks.

Twenty-eight autumn origins lack enough contiguous recent training data after recording gaps. The primary cache deliberately contains NaN Ridge profiles on those origins; they need an explicitly counted baseline fallback in a financial replay. Accuracy comparisons exclude those origins from **all** methods, rather than comparing different target coverage. There are 62 scored autumn origins and all 42 recent origins. There were no missing recent Ridge profiles. Package warnings from linear algebra were emitted during fitting, but accepted prediction arrays were checked for finite values; NaNs only mark the documented unavailable profiles.

Review caught an unsafe gap fallback in the causal-context helper. It was repaired as described above and four targeted regression tests cover gapped histories, insufficient samples, truncated windows and invariance to future meter changes. All 134 daily and 672 intraday profiles were regenerated with unchanged model configuration. Exact numeric comparison against the prior accepted cache found **zero changed origins or price-independent load-profile columns**, including the seven-day mean. None of the tested origins exercised that fallback; forecast metrics and financial runs consuming the previous mean profiles therefore remain unchanged. [Exact comparison](output/emhass_forecast_gap_repair_diff.json), [regression tests](../tests/test_emhass_forecast_causality.py).

## Reproduction and cache interface

```bash
.venv-emhass/bin/python analysis/evaluate_emhass_forecasting.py --holdouts-only
.venv-emhass/bin/python analysis/evaluate_emhass_forecasting.py --intraday-only
.venv-emhass/bin/python analysis/evaluate_emhass_pv.py
```

`--holdouts-only` retains the existing frozen model selection. A default forecasting-script run repeats the original development selection before holdouts; use the frozen path for comparison reproduction.

* Daily demand: `data/emhass/forecasts/{2025_autumn,2026_recent}/YYYY-MM-DD.csv`. Columns are timestamp, asof, seven_day_mean, same_weekday, mean_first_step_mix, RidgeRegression. Each profile has 192 steps, starting at local midnight; parse its actual timezone offset, do not infer UTC midnight from its filename.
* Quarter-hour demand updates: `data/emhass/forecasts/intraday_2026-10-12_2026-10-18.npz`. Arrays: `asof_utc_ns` (672 signed int64 nanosecond timestamps), `load_w` (672×192), `lead_minutes` (0,15,...,2865). First origin is 11 October 2026 22:00 UTC, meaning 12 October 00:00 Prague.
* Solar: `data/emhass/forecasts/pv/{2025_autumn,2026_recent}/YYYY-MM-DD.csv`, with baseline_pv_w and emhass_adjusted_pv_w.
* [Cache manifest](../data/emhass/forecasts/manifest.json) records input hashes and installed package versions; [PV configuration](output/emhass_pv_frozen_spec.json) freezes the predeclared package-default correction.

Actual package versions were EMHASS 0.18.6, pandas 3.0.6, numpy 2.2.6, scikit-learn 1.9.1 and skforecast 0.19.1. The 192-step forecast uses the actual skforecast forecaster fitted by `MLForecaster.fit`; the package convenience `MLForecaster.predict` ties its number of steps to its lag count, so the fitted object's prediction method is used to retain a 48-hour horizon with 96 lags.

The useful package facilities tested were rolling optimizer entry points (separate controller comparison), autoregressive/calendar ML fit and prediction, two estimator families, measurement blending, learned GPS-aware solar correction with time-series validation, and battery identification. Alternate cloud forecast providers require different inputs/accounts and were not silently substituted. Released EMHASS remains a point-forecast optimizer; its documented chance-constrained research study is not a production runtime option. [Official forecast implementation](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/forecast.py), [ML forecaster implementation](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/machine_learning_forecaster.py), [battery estimator](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/battery_identification.py).
