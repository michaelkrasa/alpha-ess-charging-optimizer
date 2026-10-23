# Hedging and partial-window charging validation — 23 October 2026

Implementation and configuration: [Hedging](../docs/HEDGING.md).

## Scope

EMHASS remains the optimizer. New opt-in policies add a bounded energy hedge,
five-minute fixed-power charge prefixes with durable stop deadlines, and
periodic full-charge/taper opportunities. Example defaults remain conservative. The authorized installation was physically
commissioned and activated as recorded below. Battery reserve behavior is unchanged.

The maintenance requirement is treated as SOC-calibration operation rather than
an assumed quantified reduction in LFP degradation. The interval is configurable;
14 days is an example from AlphaESS residential guidance, not a universal battery
lifetime rule. The physical model cannot establish cell balance or BMS calibration.

## Evidence protocol

Compare four policies on identical inputs: current whole-quarter EMHASS,
partial charging alone, partial charging plus hedging, and those features plus
14-day maintenance. Replay continuous 12–18 October 2026 and 10–16 November 2025
windows with the existing measured power, causal demand/PV caches, tariffs,
initial SOC, efficiencies, wear and inferred EV interlocks. These periods have
been inspected during development and are not untouched holdouts.

Original-resolution physical segments are split exactly at timed charge stops.
The short-charge policy replans after its stop using observations available at
that decision time. More distant partial actions require a new commitment.
No future measured consumption or PV enters forecasts or the hedge's empirical
error histories. Completed historical trajectories are used only on later days.
Daily cached forecast issue timing differs from the new production first-available
forecast archive, so replay coverage is not a production calibration guarantee.

Keep the existing prespecified terminal inventory mark (EUR 0.10 per delivered
kWh), and additionally run a common native-only measured continuation with grid
charging off until all policy batteries converge. This continuation values
ending stock; it is not continued rolling optimization. Report its availability
and time to convergence. Cash alone is not comparable when ending inventory
differs. Include losses and modeled discharge wear in economic results.

Score expensive-period imports using each published day's upper import-price
quartile. Report failures, physical energy conservation, reserve/EV violations,
early stops, full-charge opportunities, empirical versus provisional hedge
plans and daily forecast-premium reservations. The daily allowance limits
forecast insurance reservations, not the realized bill difference.

## Historical results

Positive cost differences below mean the new policy costs more. Both ending
inventories were physically settled to the same 1.42 kWh reserve. The common
continuation required 9.90 hours after the recent week and 2.25 hours after the
autumn week. Primary marked-inventory economic results remain available in the
private reports; the table uses the supplementary common continuation.

| Period | Policy versus whole-quarter EMHASS | Expensive imports reduced | Cash cost difference | Cost including wear difference |
| --- | --- | ---: | ---: | ---: |
| 12–18 Oct 2026 | Partial charging | 0.086 kWh | €-0.032 | €+0.032 |
| 12–18 Oct 2026 | Partial + hedge | 3.109 kWh | €-0.091 | €+0.239 |
| 12–18 Oct 2026 | Partial + hedge + maintenance | 3.109 kWh | €-0.087 | €+0.238 |
| 10–16 Nov 2025 | Partial charging | 0.759 kWh | €-0.074 | €-0.016 |
| 10–16 Nov 2025 | Partial + hedge | 2.950 kWh | €-0.163 | €+0.269 |
| 10–16 Nov 2025 | Partial + hedge + maintenance | 2.950 kWh | €-0.163 | €+0.269 |

Across the two weeks, the hedge reduces expensive-quarter imports by **6.060
kWh**. Its cash cost is **€0.253 lower**, while cost including modeled wear is
**€0.508 higher**. Adding periodic full charging gives **€0.250 lower cash cost**
and **€0.507 higher cost including wear**. Partial charging alone is nearly
neutral: €0.106 lower cash cost and €0.015 higher cost including wear.

This supports an opt-in insurance preference, not a demonstrated bill-saving
upgrade. Retain conservative defaults. The user may explicitly accept a small
expected wear/cost premium to reduce exposure to high-price imports. Do not
annualize these inspected weeks or call the result realized savings.

There were **zero planner failures, reserve violations, battery discharges during
inferred EV sessions or energy-balance assertion failures** across 5,376 rolling
replans. Whole-quarter/partial/hedged/maintenance first-action projections numbered
1/1/2/2 in each week, reflecting existing executable-window limits. The hedged
policy performed 8 recent / 18 autumn short stops. It used empirical cumulative
errors for 447 recent / 216 autumn plans and provisional stresses for 11 recent /
0 autumn plans. Other intervals had no qualifying risk exposure.

Forecast-premium bookings made during the replay totaled €0.553 recent /
€0.350 autumn for the hedged policy. These statistics use the planning day and
can include commitments for the following execution day; they are not daily
ledger balances or realized cost limits. The execution-day ledger separately
caps reservations and retains each quarter's highest commitment. Maximum raw
daily cash increases were €0.856 recent / €0.224 autumn; daily cash can include
retained inventory. Whole-period results settle that inventory explicitly.

Maintenance adjusted charging in the recent window without increasing the
full-SOC event count relative to the hedged policy. Both policies also reached
full charge naturally. The historical simulator
counts full SOC opportunities and does not simulate BMS balancing or taper
calibration. These seven-day windows do not test a complete 14-day recurrence.
Clock/state tests verify that recurrence and require measured full/taper evidence
rather than treating a scheduled full charge as completed maintenance. No
long-term degradation benefit is priced into the results.

Replay assumes ideal timed stops and next-quarter application. Cloud latency,
write/readback delay, stale SOC and outage overshoot are not simulated in these
financial figures. The continuous server replans after a short stop using data
available at that later decision time; this assumes a timely usable measurement.
Physical stopping and latency are evaluated separately in the installation
commissioning evidence below; historical replay does not simulate them.

## Local verification

Full repository suite: **656 passed, 131 existing skips**, with one existing
Starlette/httpx deprecation warning. New tests cover actual partial-power solves
and independent physical replay, instantaneous optional import limits, causal
error archives, correlated cumulative errors, daily budget retry/restart,
maintenance timing and taper evidence, continuous-executor readiness, deadline
restart/retry, stale/fresh SOC ceilings, OFF intent and expired stops. Optional
risk-solver failure retains the certified nominal plan and physical trajectory.
Focused lint and whitespace checks pass. Runtime dependencies and lockfile are
unchanged.

The final 131-interval private full-horizon fixture passed six local solves
(cold/cached at SOC 60.8%, 61.6% and 62.4%) with independent physical replay.
After polishing, solve times were 0.043–0.092 seconds and certified MIP gaps
were at most 0.000968,
below the declared 0.001 limit. Hedging and due maintenance were enabled.

## Actual NAS performance

The corrected isolated check passed on the ARM64 NAS using the existing runtime
image with the candidate source overlaid read-only, no network or credentials,
one solver thread and a 1 GiB memory limit. It did not replace or control the
live service. CPU quotas were omitted because the NAS kernel does not support
them; the first quota-limited attempt failed before solving.

Both configurations passed six 131-interval solves: cold/cached at measured
starting SOC 60.8%, 61.6% and 62.4%. Every result was Optimal, its independent
physical SOC replay passed, and its MIP gap was below the declared 0.001 limit.

| NAS configuration | Cold solve range | Cached solve range | Maximum MIP gap |
| --- | ---: | ---: | ---: |
| Partial charging + hedge | 4.729–7.413 s | 1.301–1.341 s | 0.000585 |
| Partial charging + hedge + due maintenance | 5.019–15.374 s | 1.275–1.304 s | 0.000816 |

This satisfies the target-host full-horizon performance check for this fixture
and these SOC cases. It does not establish worst-case performance across all
prices, forecasts or operating conditions.

Implementation, local tests, paired historical replay and target-host solver
validation are complete. Physical commissioning and conditional activation
completed on the authorized installation on 23 October 2026.

## Physical commissioning and activation

A bounded 15-minute charging window was stopped after five minutes using the
actual deadline executor, under the existing durable writer lease. Readback
completed 34 seconds after the requested stop. The five-minute cloud samples
showed approximately 8.5 kW grid charging before that confirmation, then zero
grid charging five minutes before the natural window end and again at its end.
SOC remained well below full, excluding full-SOC saturation as an explanation.

The first verifier incorrectly excluded samples exactly at the requested stop
and natural endpoint. Its corrected protocol requires a charging sample before
the actual confirmation, two stopped samples no later than the window end, and
the first stopped sample at least two minutes before expiry. Late commands,
natural expiry alone, full SOC and observed restarted charging remain rejected.
The completed stop was reread from fresh inverter history without another
charging write. Durable-journal recovery preserves this evidence across failed
verification attempts; local launcher retries retain exclusive lease behavior.
A subsequent fresh history/readback/lease check passed. An earlier combined
verification rejection was transient; its individual cause was not captured.

The restricted root-owned commissioning helper and key-only SSH path were
installed and probed. They grant no general passwordless Docker or shell access.
Activation independently checked the physical certificate, inverter fingerprint
and charging power, backed up the private configuration, and recreated the sole
controller. The new active cycle and schedule readback were accepted, readiness
was clear, and management remained ON. Enabled settings are five-minute charging,
the 80th-percentile hedge capped at 2 kWh and EUR 0.10/day forecast premium, and
14-day full-charge/taper maintenance. The live 125-interval solve was Optimal in
21.615 seconds with a 0.000786 gap and no risk fallback.

At activation the remaining current-day prices did not qualify for an additional
buffer purchase under the configured loss/wear/insurance rules. Enabled hedging
is not a promise of extra charging in every quarter. The initial full-charge
opportunity was planned for the following afternoon, with a taper window; it
remains due until measured full/taper evidence completes it. This commissioning
establishes the tested physical stopping behavior, not realized financial savings.

## Reproduction and private evidence

Private evidence remains under ignored `analysis/output/` and `output/`.
Reproduce all four paired policies in one `python -m analysis.hedging_benchmark`
run per period, using tags `hedge_recent` and `hedge_autumn` as shown in the
Hedging guide. Aggregate and settle those combined JSON/CSV reports with
`python -m analysis.summarize_hedging`. `--policies` remains useful for isolated
diagnostics; the paired summary requires all four policies.

- `analysis/output/emhass_benchmark_hedge_{recent,autumn}.{json,csv}`
- `analysis/output/hedging_comparison.json`
- `output/hedge-polish-build.log`
- `output/hedge-full-horizon-131-polish.json`
- `output/hedge-nas-{hedge,maintenance}-report.json`

Additional private installation evidence: `output/hedge-commission-activated.json`,
`output/hedge-commission-history.json`, and `output/hedge-commission-activation-2.log`.

## Session-wide review and consolidation

Reviewed all modified/new files against commit
`6a0114c451de88ee18e0a776e2d4cbfdff044db0` and the accepted conversation
requirements. Two independent reviewers examined Standards and Spec separately;
both checked the fixes and reported no remaining deployment blocker.

### Standards

Four behavior findings were resolved: OFF restoration now takes priority over
partial-charge waiting; insurance holds reserve premium; larger same-quarter
revisions book only their increase; and failed connection cleanup cannot retain
an owned lease. Borrowed commissioning leases remain with their caller.

Two maintainability observations were addressed: policy deadlines and allowance
accounting are shared between live control and historical replay, and fresh/
recovered commissioning evidence uses one certification path. Pure deadline
merging, due/SOC checks and masking now sit together. The controller retains
physical writer ownership. New attempts reset prior certification fields, and
all lease-owned commissioning work is covered by cleanup.

### Spec

Two behavior findings were resolved: full-charge deadlines no longer become a
rolling four-hour window, and import-blocked maintenance remains pending instead
of making an ordinary feasible plan fail. Missed deadlines select the earliest
feasible fill; actual rolling solves confirm progress toward one completion
point. The conditional buffer remains a capped insurance preference rather than
a late-evening minimum SOC requirement.

All nine new regressions pass. Both complete historical weeks were rerun with
all four policies, yielding 5,376 successful replans and the revised financial
results above. The documented reproduction now feeds one combined report per
season directly into inventory settlement. Forecast-premium booking labels
explicitly distinguish planning day from the ledger's execution day.

### Final NAS verification after polishing

The reviewed ARM64 image was deployed through the existing passwordless updater
and verified against the locally tested image identity. The first fresh NAS
cycle solved its full 121-interval priced horizon in 3.634 seconds, Optimal,
with a 0.000992 gap below the 0.001 limit and no risk fallback. The planner's
independent physical SOC replay passed. Management remained ON, readiness was
clear, schedule readback was accepted, and the existing physical commissioning
certificate and enabled policy settings were preserved.

The read-only dashboard preview passed HTML/live-state checks and rejected a
control POST with405. A changed local VPN route required binding only the NAS
connections to the already available Ethernet path; no firewall or system route
was changed. Temporary preview/forwarding processes were removed afterwards.

Final review status: Standards: 4 behavior findings resolved and 2 maintainability
observations addressed; Spec: 2 behavior findings resolved. No remaining blocker.
Private evidence: `output/hedge-polish-verification.json`,
`output/hedge-polish-deployment.log`, `output/hedge-polish-build.log`,
`output/hedge-polish-preview-check.json`, and `output/hedge-review-summary.log`.
