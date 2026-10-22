# EMHASS validation and adoption — 21 October 2026

## Decision

Use actual EMHASS 0.18.6 rolling MPC as the household’s active planner. Keep the previous candidate search as a read-only comparison. Keep the EV-separated seven-day demand profile and weather/PV calibration: the tested learned forecasts did not improve consistently across seasons.

This is a tested operational improvement under the declared model, not a guarantee of future or independently measured savings. Local activation and independent schedule readback passed; evidence is recorded below.

## Comparable financial results

All amounts EUR; lower is better. Cash is variable import cost minus surplus-PV export revenue. Economic cost adds assumed battery wear and the prespecified ending-energy adjustment. Fixed charges and battery purchase cost are excluded. The same measured site consumption, PV, prices, EV interlock, initial SOC and physical model are replayed for every policy.

| Controller | Recent 42 days cash | Recent economic | Autumn 90 days cash | Autumn economic |
|---|---:|---:|---:|---:|
| No battery | 242.86 | 242.86 | 691.71 | 691.71 |
| Native solar/self-consumption battery | 214.37 | 222.00 | 682.74 | 688.76 |
| Original once-daily price logic | 192.87 | 207.06 | 680.70 | 701.85 |
| Candidate search once daily | 177.82 | 195.49 | 643.49 | 667.16 |
| Candidate search every 15 min, matched capacity | 171.85 | 191.32 | 637.54 | 663.96 |
| EMHASS MPC every 15 min | 163.16 | 184.44 | 628.54 | 658.71 |
| Prior live search, three capacity scenarios | 172.86 | 192.18 | 637.24 | 664.79 |

Recent:8 September–19 October 2026,42 complete days,one continuous segment. Autumn:1 October–31 December 2025,90 complete days,three segments after recording gaps/DST exclusion. These were inspected research periods, not a newly untouched holdout. Repeated planning uses only known prices; tomorrow enters at 15:00 Prague. Historic tariff timing is a 09:00–13:00 VT scenario, verified directly only for 20–26 October 2026.

- Recent: EMHASS reduces modeled cash cost by **€29.71 versus the original once-daily logic**, **€9.71 versus the prior live search**, and **€79.70 versus no battery**. Its economic improvement over the prior live search is€7.75.
- Autumn: EMHASS reduces modeled cash cost by **€52.16 versus the original once-daily logic**, **€8.69 versus the prior live search**, and **€63.16 versus no battery**. Its economic improvement over the prior live search is€6.09.

The small earlier “€19/year” type comparison was incremental to an existing battery strategy, not the value of having the battery. Earlier replays also incorrectly let the battery supply EV demand and used a removed 20 kW assumption. Those figures do not validate this corrected controller. Do not annualize these selected seasonal windows.

## What 15-minute feedback adds

Holding the candidate algorithm and forecasts constant, rolling updates reduce cash cost versus its once-daily version by€5.97 recent / €5.95 autumn. EMHASS then adds another€8.69 / €8.99 versus that rolling search. Frequency alone is not the whole gain; the optimizer, demand model and executable schedule matter.

Fresh SOC corrects deviations from forecast every quarter hour. Forecasts remain uncertain: known prices do not reveal future cooking, heating, EV availability or cloud cover. Full-night charging is allowed when its expected avoided imports outweigh losses, wear and displaced solar capture. The model may reach 100%; the 10% reserve remains enforced in replay.

## Ending stock: why the first weekly bill was misleading

In 12–18 October, the search ended at 10% SOC and EMHASS at 73.19%. EMHASS’s raw bill was€1.16 higher; under the predeclared€0.10/usablekWh residual value its economic cost was€0.27 higher. Both longer primary periods end with matching battery energy, so this ambiguity does not drive their result.

As a supplementary check, stop grid charging after the week and run both batteries under the same native self-consumption policy using the next measured day. They converge to 10% after 8.5 hours. EMHASS’s extra stored energy avoids€2.36 of grid purchases, with€0.38 additional wear; the complete week-plus-liquidation difference becomes **€1.19 cash / €0.85 economic in EMHASS’s favor**. This values ending stock; it is not a claim that the native continuation is continued rolling optimization. The original primary endpoint is retained.

![Prices, battery stock and cumulative cash cost](output/emhass_week_inventory.png)

Eight 7-day sensitivity cases covered nominal 17.2 kWh capacity, zero/high wear, efficiency 0.985, broader EV blocking, live-refreshed Ridge demand and learned solar correction. Under the original short-week mark, most favor the search; under common physical liquidation, all favor EMHASS by€0.62–€1.42 cash / €0.33–€1.30 economic. Report both: the horizon boundary changes the interpretation. No weekly confidence interval is reported because a single seven-day block cannot support it.

The 42/90-day paired, descriptive seven-day block bootstrap intervals for the matched-capacity EMHASS cash benefit are€4.54–€12.09 and€5.28–€12.81. Economic intervals also remain positive. These are descriptive resamples of inspected historical data, not independent future-profit confidence guarantees.

## Actual package tools and household data

- 473 complete measured days prepared; five-minute site demand and PV remain intact for billing. EV is inferred from sustained load steps, battery power balance and non-falling SOC, allowing concurrent battery charging. Dedicated charger readings are unconnected/zero. During inferred EV charging, the simulator prevents **all** battery discharge. Imputed household demand is training data, never scored as independently measured truth.
- The newest 11 hours of forecast context are rebuilt from strictly preceding samples, preventing completed-session EV labels from leaking future information. Gaps use raw observed site demand/NaN, never retrospective imputations. An audit of 806 origins found the corrected fallback changed none of the frozen evaluation forecasts.
- Actual EMHASS Ridge/calendar forecasting improves recent daily MAE 438.71→411.25 W but worsens autumn 783.59→965.65 W. Quarter-hour context refresh improves next-step MAE 393.87→261.12 W in one week; last-measurement persistence already achieves 271.99 W. This did not establish a stable financial improvement.
- Actual GPS/calendar solar correction improves recent MAE 187.95→175.23 W but worsens autumn 87.91→102.98 W. Existing location, orientation and archived weather forecasts were used; there was no separate authenticated office-system feed or archived temperature series to invent.
- Actual battery identification estimates 14.17/14.35 kWh in 2026/2025. This supports the 14.2 kWh SOC-coordinate planning model. Its high round-trip estimates depend on derived site-balance power; production keeps 95% one-way efficiencies, with a 0.985 sensitivity. Current 2026 night charging observations cluster around 8.49 kW;8.6 kW remains the declared model.2025 also contains a lower-power regime, so its replay is a current-hardware scenario, not reconstruction of historical inverter settings.

Detailed methods and citations: [package research](emhass_integration_research.md), [forecast evaluation](emhass_forecast_evaluation.md), [frozen protocol](emhass_benchmark_protocol.md).

## Hardware and execution checks

The actual `perform_naive_mpc_optim` solver runs directly from Python; Home Assistant is unnecessary. An explicit package extension models fixed-power grid charging clipped at full SOC, automatic PV capture and permission-only household discharge. It uses the same economics as physical replay, including a tested terminal-energy objective. There is no default grid-import cap and no battery export. The optional explicit cap is honored when configured.

Each optimized forecast trajectory is independently replayed and its SOC checked at every quarter. Financial outcomes then use original-resolution measured load/PV and actual encoded 2 charge+2 discharge windows, refreshed each 15 minutes. Up to two periods per direction can be installed at a time; repeated rewrites allow more changes over a day. The weekly API remains unavailable (6017).

Executable mapping preserves the current representable quarter and separates calendar dates. Unsupported midnight modes are projected and counted; the 23:45 endpoint, repeated wall-clock DST hour and two-endpoint non-atomic writes remain real limitations. The benchmark assumes ideal next-quarter atomic application, with no network/write jitter; live writes instead use serialized, guarded staging/readback. These simulations are evidence for selecting a planner, not exact measured invoice reconciliation.

All replays recorded zero reserve violations, zero battery discharge during inferred EV intervals and no battery export. There were no solver-infeasibility failures. Missing final published horizons caused 5 recent / 15 autumn documented short-horizon fallbacks at archive/recording boundaries; unknown future prices were not fabricated.

Dynamic integer SOC targets are a plausible finer-control alternative, but arbitrary-target stopping/PV behavior has not been physically commissioned. An actual continuous EMHASS projection quantified integer endpoint rounding (~0.052 kWh mean); it does not establish executable profit. This option is documented rather than falsely declared impossible. The selected implementation keeps currently used 100/10 targets.

## Validation and independent review

Final local suite: **555 passed,131 existing skips**. Actual EMHASS solver tests cover energy/objective units, SOC saturation, PV surplus, negative prices, optional caps and a 25 kWsite-load case with no invented limit. Other new checks cover EV causality, original-resolution energy conservation, DST/overlap rejection, and failure isolation for either inactive planner. Source compilation, focused lint and whitespace checks pass.

### Standards

Four behavior findings resolved: ambiguous DST windows, ignored explicit optional cap, inactive-candidate failure stopping active EMHASS, and misleading control-ownership wording. The shared horizon validator and pair checks close the command hazards. One heuristic maintainability observation remains: schedule decoding is repeated in several modules; it is not an outstanding correctness finding.

### Spec

Four findings resolved: missing live/shadow selection wiring; causal EV-gap fallback; UI claiming control while disabled; and mathematically degenerate one-week confidence intervals. Both inactive planners fail independently of the selected writer. Same-SOC comparisons and their status are visible in the dashboard.

Standards: 4 behavior findings resolved, 1 maintainability judgment remains. Spec: 4 findings resolved; no remaining confirmed spec defect. All financial figures remain conditional on inferred EV labels, aggregate battery physics, current-tariff scenarios and historical demand/weather.

## Local operation and evidence

```bash
uv sync --extra emhass
uv run --extra emhass python main.py serve --live
```

Python 3.12/3.13 is declared; this run uses 3.12. EMHASS 0.18.6/CVXPY 1.7.5/HiGHS 1.15.1/NumPy 2.2.6/pandas 3.0.6 match the replay stack. The local dashboard is [http://127.0.0.1:8765](http://127.0.0.1:8765), with no username/password. `standard_planner: emhass`, `emhass_shadow: true`, and EV separation are enabled. The candidate algorithm remains solely for read-only comparison and reproducible baseline analysis. It is not a second writer. No AWS/NAS deployment was performed. The optional EMHASS stack is not part of the existing small Lambda ZIP.

Artifacts: [primary recent](output/emhass_benchmark_recent_mean.json), [primary autumn](output/emhass_benchmark_autumn_mean.json), [prior live baseline recent](output/emhass_benchmark_recent_live_baseline.json), [prior live baseline autumn](output/emhass_benchmark_autumn_live_baseline.json), [summary/sensitivities](output/emhass_comparison_summary.json), [inventory settlement](output/emhass_inventory_week_mean.json), [live shadow check](output/live_emhass_shadow_2026-10-21.json).

### Final live check

On 21 October at 14:01 Prague, EMHASS was the active planner, control was ON, and both endpoint settings matched independent readback. The 14:00 cycle completed with a 0.13-second solver comparison. Charge was off; household supply windows were 15:30–19:45 and 20:00–20:30, with a 10% reserve. Current SOC was 49.2% and future prices/forecasts were shared with the read-only candidate comparison. Acknowledgment confirms stored settings, not independently metered physical execution or realized profit.

[Live adoption evidence](output/live_emhass_adoption_2026-10-21.json).
