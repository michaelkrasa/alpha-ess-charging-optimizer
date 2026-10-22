# EMHASS integration research — 21 October 2026

## Decision-relevant findings

EMHASS 0.18.6 is the current PyPI release inspected for this work. Its actual rolling entry point is `Optimization.perform_naive_mpc_optim`; this is a receding-horizon solve, with fresh initial SOC and forecasts supplied on each call. It is usable directly from Python without running Home Assistant. This does **not** make its continuous battery setpoints directly executable by our AlphaESS time-window controls. [Release](https://pypi.org/project/emhass/0.18.6/), [pinned implementation](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/optimization.py), [rolling guide](https://emhass.readthedocs.io/en/latest/study_cases/mpc.html).

The defensible comparison has two EMHASS results: continuous-control potential and the **same plan translated to the real fixed-power charge / self-consumption-permission controls, then replayed with actual household outcomes**. Only the latter can justify replacing the live controller. The EV must be a separate, battery-ineligible grid load in both training and replay; subtracting an assumed 6–11 kW from every large household peak is not a verified EV meter.

## Concrete direct-Python interface

The released signatures are:

```python
Optimization(
    retrieve_hass_conf, optim_conf, plant_conf,
    var_load_cost, var_prod_price, costfun, emhass_conf, logger,
    opt_time_delta=24, num_timesteps=N,
)

optimizer.perform_naive_mpc_optim(
    df_input_data, p_pv, p_load, prediction_horizon=N,
    soc_init=current_soc_fraction, soc_final=terminal_soc_fraction,
    soc_target=None, soc_target_timestep=None,
    current_period_peak=None, capacity_charge_window=None,
    capacity_charge_consideration=None,
    capacity_charge_current_interval_history=None,
    def_total_hours=None, def_total_timestep=None,
    def_start_timestep=None, def_end_timestep=None,
    stage_times=None,
)
```

Supply equally indexed pandas objects: `df_input_data` has the named import and export tariff columns in **EUR/kWh**; `p_pv` and `p_load` are Series in **watts**, capacity is **Wh**, SOC is a fraction. Construct with `num_timesteps=N` to match the passed horizon. The method rejects fewer than five timesteps, so a short final published-price tail needs an explicit fallback or a longer independently justified tariff horizon. Cache separate optimizer instances by horizon length if necessary. [Pinned implementation](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/optimization.py).

Load the package's `config_defaults.json` and categorize keys using its `data/associations.csv`, or call its async `utils.build_params`; avoid maintaining an incomplete handcrafted config. When building dictionaries directly, `retrieve_hass_conf['optimization_time_step']` must be `pd.Timedelta(minutes=15)`, not the integer 15 used in JSON. Set `time_zone='Europe/Prague'`, the configured sensor names, no deferrable loads, and the following overrides. [Default configuration](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/data/config_defaults.json), [configuration builder](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/utils.py).

```python
optim_conf.update(
    set_use_pv=True, set_use_battery=True,
    number_of_deferrable_loads=0,
    set_nocharge_from_grid=False,
    set_nodischarge_to_grid=True,
    set_battery_first_priority=False,
    set_battery_dynamic=False,
    weight_battery_discharge=0.045,
    weight_battery_charge=0.0,
    set_total_pv_sell=False,
    lp_solver='HiGHS', num_threads=1,
    lp_solver_mip_rel_gap=0.0,
)
plant_conf.update(
    inverter_is_hybrid=False, compute_curtailment=False,
    battery_nominal_energy_capacity=capacity_kwh * 1000,
    battery_minimum_state_of_charge=0.10,
    battery_maximum_state_of_charge=1.0,
    battery_target_state_of_charge=0.10,
    battery_charge_efficiency=0.95,
    battery_discharge_efficiency=0.95,
    battery_charge_power_max=8600,
    battery_discharge_power_max=8600 / 0.95,
)
```

The asymmetric rating conversion above is intentional. EMHASS imposes both an initial `AC charge <= max_chg` variable bound and a direction-dependent `AC charge <= max_chg / eff_chg` bound: the first is tighter, so charge remains 8600 W. Discharge has both `AC discharge <= max_dis` and `AC discharge <= eff_dis * max_dis`; its effective ceiling is the latter, requiring the division above. This was verified by executing pinned first-action solves; using `8600 * .95` for the charge setting makes an 8600 W charge pin infeasible. The supplied 95% one-way efficiencies are assumptions to test, not a measured factory fact. [Battery constraints](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/optimization.py).

**Do not inherit EMHASS's implicit 9 kW grid bounds.** The code defaults `maximum_power_from_grid` and `maximum_power_to_grid` to 9000 W when absent. Set explicitly nonbinding mathematical bounds derived from the horizon's load, PV and battery ratings; they are numerical big-M bounds, not a new household import cap. `inverter_is_hybrid=False` is an AC-equivalent benchmark representation matching the existing aggregate energy model, not a claim about physical wiring. The hybrid model separately requires verified inverter ratings and conversion losses. [Grid and hybrid constraints](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/optimization.py).

## Restrictions and terminal treatment

`set_nodischarge_to_grid=True` is supported. With the AC-equivalent configuration, export is bounded by `max(PV - load, 0)`, preventing battery discharge from freeing PV for export. Battery charge/discharge are mutually exclusive through a binary direction variable. The default battery power remains continuous between zero and its maximum. There is no released standard battery option for “charge at exactly 8.6 kW or zero, automatically stopping at the target SOC” or “when discharge is enabled, cover all eligible household demand automatically.” [Pinned source](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/optimization.py).

For exact hardware modeling, subclass `_add_battery_constraints(constraints, batt_stress_conf)` and call `super()` before adding CVXPY constraints. The source exposes `self.vars['p_sto_pos'][0]`, `p_sto_neg`, `E`, `param_load_forecast`, `param_pv_forecast`, and SOC parameters. Add binary charge/hold/self-consume modes and saturation at SOC ceiling/floor; discharge must be the minimum of available battery, eligible net demand and physical rating. A simple equality `charge == 8600 * binary` is insufficient at the last partially full quarter. This is an extension of EMHASS, not stock behavior, and requires executable-replay validation. Adding only first-step mode constraints or rounding the continuous first setpoint is a useful adapter candidate, but cannot claim globally optimal hardware control.

Terminal SOC is not innocuous. The default `battery_soc_final_reward_factor=0` penalizes **both undershoot and overshoot** of `soc_final` at approximately 100 times the highest positive import tariff. Thus passing `soc_final=soc_init` effectively requests return to the starting SOC, and passing the 10% floor effectively drains to the floor at the finite horizon. With a positive reward factor, overshoot is instead valued at `factor * last_import_tariff`; e.g. when that tariff is positive, `.10 / last_import_tariff` represents a 0.10 EUR/kWh terminal energy credit. The under-target penalty remains. Compare terminal valuation strategies explicitly and do not let an unreported horizon-edge assumption create the claimed savings. [Terminal objective and parameter updates](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/optimization.py).

The guide's description of `soc_final=0` is **not a reason to pass an impossible target below our 10% reserve**. The direct method is not the same as the REST runtime normalization layer; pass valid fractions explicitly. Only use published tariffs available at the simulated decision time.

## Forecast tools that should actually be evaluated

### Household demand

`MLForecaster(data, model_type, var_model, sklearn_model, num_lags, emhass_conf, logger, weather_features=None)` consumes a timezone-aware regular pandas DataFrame; Home Assistant retrieval is unnecessary when passing data directly. `await fit(split_date_delta='48h', perform_backtest=False)` trains a skforecast `ForecasterRecursive` with calendar features, autoregressive lags, and optional weather columns. `await predict(data_last_window=latest_history, weather_future=issued_forecast)` refreshes the autoregressive context. The horizon defaults to `num_lags` (or tuned `lags_opt`), so arbitrary MPC horizons may require using the underlying fitted `forecaster.predict` with correctly generated exogenous rows. [Released ML implementation](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/machine_learning_forecaster.py).

The code supports Linear, Ridge, Lasso, ElasticNet, KNN, DecisionTree, SVR, RandomForest, ExtraTrees, GradientBoosting, AdaBoost and MLP estimators; the constructor docstring's three-model list is outdated. `tune` performs Bayesian hyperparameter/lag search. Start with KNN and GradientBoosting versus seasonal persistence and the current mean; running every estimator is not evidence of value. The important ablation is corrected battery-eligible demand versus EV-contaminated aggregate demand, with chronological training/calibration/test splits. [ML implementation](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/machine_learning_forecaster.py).

`Forecast.get_mix_forecast(df_now, forecast, alpha, beta, col, ...)` only blends the **first** forecast value with the latest measured value. It does not update the whole day's demand. It skips missing live sensors and NaN operands. This is cheap to benchmark but must not be described as a learned intraday demand model. The `forecast-calibration` action reports forecast accuracy; it does not automatically select or activate a new optimizer. [Forecast module](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/forecast.py), [forecast guide](https://emhass.readthedocs.io/en/latest/forecasts.html).

### Solar, location and weather

The forecast module supports Open-Meteo weather converted by pvlib, supplied PV power lists/CSV, Solcast and solar.forecast providers. `get_power_from_weather` accepts a weather DataFrame and can apply the same first-step measurement mix. For a fair historical test, archived forecasts issued before each decision must replace a present-day live weather request. A physical pvlib forecast needs verified panel/inverter geometry and ratings; GPS alone cannot identify array orientation, shading or nameplate capacity. [Forecast implementation](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/forecast.py), [forecast guide](https://emhass.readthedocs.io/en/latest/forecasts.html).

The package's solar correction is particularly relevant to our existing historical PV:

```python
forecast.adjust_pv_forecast_data_prep(history_actual_and_issued_forecast)
await forecast.adjust_pv_forecast_fit(
    n_splits=5, regression_model='LassoRegression', debug=True
)
corrected = forecast.adjust_pv_forecast_predict(
    future_issued_pv.rename('forecast').to_frame()
)
```

It learns bias using issued forecast, calendar variables, cyclic fractional hour, and solar elevation/azimuth calculated from GPS. It uses time-series cross-validation and can exclude known curtailment intervals. Never train it on actual future solar or revised forecasts that were unavailable at decision time. Office PV may be an additional regional cloud signal only after checking distance, orientation, meter units and timestamp alignment; its amplitude is not interchangeable with the house array. [Solar correction implementation](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/forecast.py).

### Battery identification and uncertainty

`BatteryIdentification(logger, time_step=pd.Timedelta(minutes=5))` is a pure offline estimator of usable SOC-coordinate capacity and round-trip efficiency from signed AC battery power and measured SOC. It explicitly cannot identify the separate one-way efficiencies; it uses a symmetric square-root convention. Its gaps, deep-segment sufficiency and confidence checks are useful for revisiting our 14.2/15.48/17.2 kWh scenarios, but an estimate should be reported rather than silently replacing production capacity. [Identification source](https://github.com/davidusb-geek/emhass/blob/bb4ec0f8ce155acaca61dea124bf9927d08955dc/src/emhass/battery_identification.py).

```python
from emhass.battery_identification import BatteryIdentification

identification = BatteryIdentification(logger, time_step=pd.Timedelta(minutes=5))
result = identification.identify(
    history, power_col='battery_ac_w', soc_col='soc_percent',
    configured_capacity_wh=17200,
)
print(result.to_dict())
# history: timestamp index, signed battery power W (positive charge internally;
# estimator has a sign-detection check), SOC in PERCENT, not fraction.
# Only result.status == 'ok' passed the package's confidence/sanity gates.
```

The documentation's chance-constrained MPC example is explicitly a **standalone research prototype**, not a released runtime option. Stock `naive-mpc-optim` solves one point forecast path. Its published quantile experiments do not establish that adding uncertainty always saves money; calibrated uncertainty may deliberately trade savings for fewer constraint interventions. [Official study and limitations](https://emhass.readthedocs.io/en/latest/study_cases/chance_constrained_mpc.html).

## Acceptance evidence

Use the same causal published prices, battery-eligible consumption, solar outcomes, battery physics, initial SOC, terminal valuation, fees and wear for all policies. Report cash bill and wear-adjusted cost separately, PV capture, cycles, SOC, realized versus requested actions, failed/infeasible solves and runtime. Forecast MAE alone does not justify switching. Freeze selection on development data, evaluate on a later untouched period, and include capacity/efficiency/EV-disaggregation sensitivity. A shadow comparison can run on every live 15-minute cycle without giving two independent processes authority to command the inverter.

The direct API and config findings above were inspected against PyPI's 0.18.6 source distribution and Git tag `v0.18.6` (`bb4ec0f8ce155acaca61dea124bf9927d08955dc`). No inverter commands were issued for this research.

## Executed minimal example and first-action hook

`analysis/emhass_minimal_example.py` runs with `.venv-emhass/bin/python analysis/emhass_minimal_example.py`. It loads the **installed package's actual defaults and association map**, creates an optimizer without Home Assistant, and executes four 96-slot solves: stock continuous control, pinned 8600 W AC charging, pinned hold, and pinned 1200 W self-consumption. All returned `Optimal` during this research. This is an API smoke test using synthetic tariffs and load, not household savings evidence.

The `FirstActionOptimization` subclass demonstrates an exact first-step hook: after `super()._add_battery_constraints(...)`, pin `p_sto_neg[0][0]` and `p_sto_pos[0][0]` to the charge/discharge powers produced by our actual plant model for that action and forecast. Solve charge, hold and self-consume alternatives, compare the EMHASS objective, and execute the selected discrete action. This replaces arbitrary rounding of EMHASS's continuous first step with three genuine EMHASS continuation solves. The remaining trajectory is still continuous, so quantify the relaxation/projection gap. When PV exceeds household load, a physical “hold” may still capture PV; it must not be naively pinned to zero battery flow. SOC saturation must also be reflected in the pin.

## Appendix: dynamic SOC cutoffs and the final quarter before midnight

### A real API alternative, with uncommissioned physical behavior

Dynamic SOC cutoffs are a plausible way to translate continuous EMHASS energy plans into short fixed-power bursts. They must not be ruled out merely because the present comparison uses a 100% charging cutoff and 10% discharge floor. The SDK pinned at `0.0.21`, commit `768a71f7c4bb3df1f892cde64ff74ab385d37d8d`, exposes `batHighCap` and `batUseCap` as percentage cutoffs. Its convenience methods convert supplied cutoffs with `int(...)`; its low-level `updateChargeConfigInfo` and `updateDisChargeConfigInfo` pass their supplied values through unchanged. This establishes supported request fields and SDK serialization, **not device acceptance of every value or fractional precision**. [Pinned SDK](https://github.com/CharlesGillanders/alphaess-openAPI/blob/768a71f7c4bb3df1f892cde64ff74ab385d37d8d/alphaess/alphaess.py), [pinned API schema](https://github.com/CharlesGillanders/alphaess-openAPI/blob/768a71f7c4bb3df1f892cde64ff74ab385d37d8d/docs/API.md#charge--discharge-configuration).

Manufacturer documentation independently describes configurable charge cutoffs and two charge/discharge windows in the residential installer portal. Its newer scheduling guide says charging stops at a period's target SOC, but explicitly notes that available features vary by model. This corroborates cutoff semantics without proving the behavior of this household's standard-API firmware or granting access to the newer periodic endpoint. [Manufacturer residential settings](https://docs.alphaess.com/portals/portal-for-installer/monitoring-and-maintenance/remote-configuration.html), [manufacturer scheduling guide](https://docs.alphaess.com/apps/alphaess-app/resi/modes/timecontrol).

The household's rapid-write test changed inactive times and **preserved the existing 100%/10% SOC cutoffs**. It proved repeated API acceptance/readback for those settings; it did not exercise intermediate cutoffs, charging termination, dynamic discharge floors or their interaction with solar. “Currently commissioned API settings / observed baseline model” is therefore the appropriate scope for the fixed-100/10 benchmark; even its `hold_verified` flag remains false. Configuration readback is not proof of physical execution. [Household commissioning scope](rapid_write_verification.md).

### What integer SOC targets could reproduce in principle

For capacity `C`, one-way efficiencies `eta_c`/`eta_d`, interval duration `dt`, and a continuous planned AC charge `P`, a no-PV charge endpoint is `SOC_next = SOC_now + 100 * P * dt * eta_c / C`. A candidate integer charge cutoff is `ceil(SOC_next)` (clamped to 100). A discharge cutoff `max(10, ceil(SOC_next))` conservatively avoids discharging below the requested endpoint. These are **energy-endpoint approximations**; they do not supply the continuously requested wattage throughout the quarter.

At 14.2 kWh and 0.95 one-way efficiencies:

| Quantity | Resolution |
|---|---:|
| One integer SOC point, stored energy | 0.142 kWh |
| Equivalent AC charging energy | 0.14947 kWh |
| Equivalent AC discharge energy | 0.13490 kWh |
| Full 8.6 kW charge for one quarter | 2.15000 kWh AC |

A read-only calculation used the actual continuous EMHASS solve for the published horizon on 12 October 2026, with an explicitly assumed initial SOC of 50%, the existing causal household forecasts and no API commands. Independently rounding its 95 SOC endpoints upward produced mean boundary quantization of 0.05168 kWh and maximum 0.14170 kWh. In one no-PV quarter at 05:00 Prague, EMHASS requested 5.0714 kW average charging, moving SOC from 64.3838% to 72.8659%. A 73% cutoff would nominally require an 8.6 kW burst of approximately 8.985 minutes. This demonstrates useful **nominal energy resolution**, not proven inverter tracking or savings. The calculation rounds boundaries independently rather than simulating a sequential target-controlled device. [Offline calculation and caveats](output/emhass_soc_target_projection.json).

Before calling that adapter physically faithful, the following semantics remain unresolved on this household: whether grid charging stops and stays inhibited at an intermediate target while its window remains active; whether native solar charging continues above `batHighCap`; whether charge-cutoff or discharge-cutoff changes affect native operation outside the scheduled periods; target hysteresis, SOC display quantization, response delay and restart behavior. PV charging and household discharge can move SOC within the same quarter, so matching an endpoint does not by itself match gross AC throughput. The manufacturer describes native self-consumption as solar-first with surplus stored, but does not map that behavior to an intermediate standard-API `batHighCap`. [Manufacturer self-consumption description](https://docs.alphaess.com/apps/alphaess-app/resi/modes/self-consumption.html).

Temporarily raising `batUseCap` could in principle stop household discharge above the permanent 10% minimum. Raising that floor does not violate the user's minimum reserve, but it changes the operational cutoff and requires tracked restoration to 10% when control ends. The current code intentionally requires the read-back discharge cutoff to equal the configured reserve, so this is **not an already implemented control path**. In particular, do not assume that writing a cutoff at current SOC is equivalent to a verified hold command, or that a higher cutoff cannot interact with other firmware reserve behavior.

The current fixed-100/10 comparison therefore cannot reject the whole EMHASS package or establish that standard-API control has only three possible power trajectories. It evaluates the currently modeled setting subset. Dynamic SOC targets remain a documented, uncommissioned alternative that can be explored offline without claiming real financial gains or changing the live controller.

### Can `ctrDis=0` cover 23:45–00:00?

The pinned API reference describes `ctrDis` as enabling **battery discharge time control**, while times are limited to 00:00–23:45. In the application's observed-baseline model, `ctrDis=0` means native self-consumption without the time gate. Consequently, when the current quarter is already self-consumption, the next 23:45–00:00 quarter also needs self-consumption, grid charging is inactive, and native operation is the intended continuing mode, disabling the discharge time gate is a representable candidate. It needs no impossible `24:00` end and does not require a crossing-midnight discharge window. This is a model-consistent inference, not a newly commissioned firmware result. [Pinned API fields and time contract](https://github.com/CharlesGillanders/alphaess-openAPI/blob/768a71f7c4bb3df1f892cde64ff74ab385d37d8d/docs/API.md#getdischargeconfiginfo).

That global switch changes behavior immediately and remains in force beyond midnight until replaced. If the current quarter must be held, enabling native discharge early would change it; if 00:00 must switch to hold or charge, the same upload cannot automatically encode “native until midnight, then another mode” using only a disabled global time gate. A subsequent boundary update or another independently supported mechanism is needed. It also does not solve the unrepresentable final-quarter **grid charging** window. The current financial comparison is unchanged; any native fallback must be represented explicitly and its subsequent updates included in the replay.

No device calls or writes were made for this appendix. The older API reference's once-per-24-hour wording is not reinstated: household rapid-write evidence already disproved that restriction for the tested endpoints/settings.
