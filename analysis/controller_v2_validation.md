> Superseded operating assumption: [21 October live repeated-write test](rapid_write_verification.md) demonstrated that this household does not enforce the documented 24-hour write restriction. Live control now replans every 15 minutes. Daily-only financial results below remain historical diagnostics.

> Follow-up: [21 October full review and live observation verdict](final_review_2026-10-21.md). Software defects corrected; hardware commissioning remains incomplete.

# Corrected household controller validation — 20 October 2026

This report supersedes the earlier charge-only and ideal timed-executor reports. It is a simulation of implementable daily settings, not proof of better realized bills. The replacement improves some periods and loses slightly on the recent period. It remains in monitoring/planning mode because physical execution and import protection have not been commissioned.

## Comparable economic results

Lower cost is better. Economic cost is import minus surplus-PV export revenue, plus estimated battery wear, minus terminal stored-energy adjustment. Positive improvement means the replacement beats the original algorithm.

| Period | Original at midnight | Original at matched 15:00 cadence | Native battery | Replacement | Improvement vs original |
|---|---:|---:|---:|---:|---:|
| 2025 (345 usable days) | €1651.47 | €1659.54 | €1627.54 | €1598.97 | €+52.50 |
| Sep 8–Oct 19, 2026 (42 days) | €206.89 | €207.27 | €224.85 | €208.07 | €-1.18 |
| August 2026 development (31 days) | €90.21 | €90.37 | €94.53 | €86.68 | €+3.53 |
| July 2026 separate check (18 days) | €30.98 | €29.53 | €26.22 | €25.80 | €+5.17 |

The 2025 window is 345 usable days, not a full observed year. Do not annualize the short summer checks. The corresponding replacement cash electricity costs, excluding wear and terminal adjustment, are €1,474.61, €197.80, €77.81 and €16.84. Recent original cash cost is €192.76: the replacement costs €5.05 more on electricity alone there, partly offset by less estimated wear. This is not consistent outperformance.

The previously calculated same-coverage solar-without-battery cash baseline is €1,780.90 for 2025 and €241.36 for the recent period. Against that baseline, the corrected managed battery saves about €306.29/€43.55 in cash, or €181.93/€33.28 after modeled wear and terminal adjustment. Thus incremental scheduling benefit is distinct from the battery's total benefit. Capital cost and fixed standing charges are excluded. The no-battery figures are from `output/no_battery_comparison.json`; its older managed/native figures are superseded here.

## What was corrected

- The median daily load profile removed much intermittent demand. The replacement uses the mean of time-integrated quarter-hour demand from at least six of the last seven completed days. No current/future actual load enters forecasts.
- The earlier model assumed 5 kW charging. Read-only household balance observations show charging around 8.6 kW, including at night. Using 5 kW could choose short windows that do not have the modeled behavior. Current fixed-power simulation uses 8.6 kW and never pretends the old API can enforce a variable power setpoint or dynamically clip import.
- The daily API exposes two charge and two discharge windows. Both are now optimized. Discharge windows allow household self-consumption; outside them the battery is preserved. It cannot intentionally export battery energy.
- Full-night charging to 100% is an explicit candidate, scored against original and native settings, PV opportunity, efficiency losses and wear. It is selected when its forecast economics win. A scenario test verifies a nominal 17.2 kWh battery fills even when the planner's fitted SOC scale is smaller. Solar-rich and flat-price tests prevent unconditional cycling.
- Candidate scoring considers the complete published horizon, typically through tomorrow evening after 15:00. A 24-hour-only horizon could miss the following evening peak.
- Both endpoint payloads and write budgets are validated before transmission. Writes have independent persistent 24-hour budgets, reserve an attempt before transmission, and require readback. Charge/discharge replacement is not atomic; partial or ambiguous changes remain unconfirmed. OFF must clear both controls and is subject to these budgets.

Known prices do not imply known household load or PV. Replanning cannot correct a schedule every quarter hour when settings can be written only once per 24 hours per endpoint. Forecast-score dominance over an admissible original candidate does not imply realized-cost dominance. Candidate search is bounded, not a proof of the global optimum.

## Walk-forward method and separate check

Actual household load and solar drive realized simulation only after decisions. Forecast load uses completed prior days. Archived IFS weather uses a six-hour publication lag. Tomorrow's prices become visible at 15:00 Prague; next-day settings first apply on a following quarter hour. Original-midnight and matched-cadence references are both retained. State carries between days in each contiguous segment; common segments start at 50%. Terminal energy is adjusted with a prespecified €0.10 per usable kWh. No concurrent battery charge/discharge and no battery export are simulated.

The model uses current 2026 invoice tariff terms, CNB EUR conversion, 21% VAT, export spot minus fee, and VT 09:00–13:00. This is a counterfactual historical tariff scenario, not a reconstruction of 2025 invoices. The household HDO schedule is verified only for 20–26 October 2026 and must be rechecked before expiry.

After discovering the charging-power error, August became a development comparison. The final financial policy was frozen before evaluating separately fetched July strategy costs: see `output/validated_policy_freeze.json`. July has only 18 usable days in three segments because gaps and six-day warm-up requirements exclude others. July costs were not used to select subsequent policy changes. All four result JSONs and CSVs are under `output/validated_*`; exclusions and physical assumptions are stored with each result. After the freeze, unused imports were removed and a reporting-only bug was corrected: comparison costs now use raw scenario costs, rather than an infinite sentinel when native is excluded by the dominance filter. Selection logic and the replayed actions are unchanged.

## Physical uncertainty and limits

The API reports nominal capacity 17.2 kWh. September/October SOC-energy episodes suggest an effective scale near 14.1 kWh and symmetric efficiency near 94%; that noisy fit is not a factory capacity measurement. The central simulation uses 14.2 kWh and 95% efficiency each way, while candidate scoring spans 14.2, 15.48 and 17.2 kWh. Wear is an assumed €0.045 per discharged kWh. Configured discharge power 8.6 kW is an assumed upper bound; observed peak discharge was about 7.3 kW. Reproduce the development-only fit with `uv run python analysis/calibrate_household.py`.

A separate recent replay at nominal 17.2 kWh gives replacement economic cost €207.18 versus original €206.73 (a €0.45 loss), consistent with the central recent result's lack of demonstrated superiority. This is sensitivity analysis, not a newly selected policy.

A recent peak-load envelope is used to reject forecast charge windows without expected headroom, but unexpected load still breaks the 20 kW planning limit. Replacement: 10 intervals in 2025 (peak 21.25 kW), four recently (peak 21.10 kW), none in August or July. Original: 13 intervals in 2025 and none recently. Even native operation has one 2025 interval above 20 kW. These are quarter-hour averages; instantaneous peaks may be higher. The invoice says 3×32 A, about 22.1 kW aggregate at unity power factor, but per-phase headroom and inverter protection remain unverified. A forecast check is not an active import limiter.

All tested policies respect the same 10% reserve in simulation. Recorded historical SOC fell below 9.5% on 142/365 2025 days and 10/49 recent days. Recorded meter cash cost is therefore a useful diagnostic, but has different constraints and unknown hardware behavior; it is not evidence of a controlled baseline. The simulation does not model API failures, exact write jitter, phase currents, battery temperature, ageing, backup reserve behavior or inverter firmware details. Successful configuration readback would still not prove physical execution.

## Current runtime and verification

The updated service runs locally on the Mac at http://127.0.0.1:8765 with SQLite persistence. It monitors every 15 minutes and proposes daily settings. Control intent is ON, but `device_control_verified: false` blocks all writes. Existing inverter schedules remain unchanged; no AWS or NAS deployment occurred.

455 tests pass, 131 are skipped. Focused tests cover profitable full-night charging to 100%, solar headroom, no needless flat-price cycling, reserve, energy balance, paired actuator preflight/readback and durable write limits. These establish software properties; they do not establish household hardware behavior or guaranteed profit.

Reproduce a result, for example:

```sh
uv run python analysis/backtest_household.py --forecast weather --start 2026-07-01 --end 2026-07-31 --policies native legacy legacy_matched standard_daily --output analysis/output/reproduced_july
```
