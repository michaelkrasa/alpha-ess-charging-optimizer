# Forecast-risk buffer, partial charging and periodic full charge

Implemented as opt-in EMHASS policies. Existing installations retain their
current behavior until configuration and commissioning are updated. Historical
results and remaining checks are recorded in
[hedging validation](../analysis/hedging_validation.md).

## Configuration

Use the continuous server with the standard-window EMHASS planner:

```yaml
control_mode: legacy_daily
standard_planner: emhass
charge_step_minutes: 5
hedge_enabled: true
hedge_quantile: 0.8
hedge_max_kwh: 2
hedge_premium_eur: 0.10
hedge_min_days: 14
full_charge_interval_days: 14
# Set only after short-stop/readback/measured-power commissioning:
early_stop_verified: false
```

Defaults are whole-quarter charging (`charge_step_minutes: 15`), no hedge and
no periodic maintenance (`full_charge_interval_days: 0`). Risk and maintenance
require the standard-window EMHASS planner. Maintenance requires a 100% maximum.
Partial live charging additionally requires the continuous server's deadline
executor; one-shot commands and Lambda cannot commission this mode.

Keep installation settings private. Start in observation mode and review
`/api/state`: `charge_policy`, `full_charge`, `early_stops` and
`early_stop_health` expose planning and execution evidence. Neither a code
change nor passing tests commission the new physical behavior. Retain one
writer, tariff verification and the existing readiness checks.

## Dynamic energy buffer

The allowance protects against higher household consumption and missing PV
before an upcoming expensive exposure. It is available to supply the household;
the permanent reserve is unchanged. The planner reconsiders buying it before
the cheap opportunity closes, rather than waiting for expensive imports.
`hedge_max_kwh` is a ceiling, not a minimum SOC floor or a promise to avoid
late-evening imports. Price comparisons use the complete tariff, losses and
modeled wear, not spot prices alone.

The controller retains the first available forecast for each target quarter,
including its issue timestamp. It scores completed, covered measurements only,
requires at least two hours' forecast lead, and separates inferred grid-only EV
load when configured. Forecast revisions cannot erase the original error.
Evidence is retained for 60 days; private household data stays in SQLite.

For the next worthwhile expensive exposure, past complete-day error trajectories
are aligned by local quarter. Load and PV errors remain correlated. The hedge
uses the configured quantile of maximum cumulative net-demand error, converted
to stored energy with discharge efficiency and capped by `hedge_max_kwh` and
headroom. Future days and incomplete exposure trajectories are excluded.

Before `hedge_min_days` trajectories exist, the allowance uses an explicitly
provisional +20% load / -30% PV stress. This is not calibrated percentile coverage.
The first implementation uses net-energy residuals rather than claiming a full
stochastic physical battery model; power limits, EV interlocks and solar
saturation still require physical replay and coverage checks.

EMHASS first solves its normal economic plan, including maintenance when due.
A second solve can reward up to the bounded extra energy immediately before
the expensive exposure. Its maximum objective reward is the remaining insurance
allowance. This preserves the economic trade-off between unused stock, losses,
wear and solar headroom. It does not force the full buffer regardless of cost.

`hedge_premium_eur` is a daily **forecast insurance allowance**, not a guaranteed
limit on the realized bill difference. Before transmitting any committed
insurance deviation, including extra charging or holding instead of discharging,
the controller reserves its forecast premium. Each quarter retains its highest
reservation: identical retries add nothing, larger revisions add only the
increase, and smaller revisions never refund an ambiguous write. Legacy ledger
totals are retained conservatively; reservations survive restart and ambiguous
failures. The bank resets for the committed action's local execution day. Forecast errors, solver tolerance and future replanning
mean realized costs can exceed the allowance; replay must report those costs.

## Charging inside a quarter

EMHASS chooses a fixed-power charging prefix of 0/5/10/15 minutes. The remainder
of a partial quarter holds the battery while allowing native PV capture. Prices
retain their market-quarter timestamps. Instantaneous optional import limits
use full charging power, rather than the misleading quarter-average power.
Every solved SOC trajectory is independently replayed through the physical model.

The compiler installs the next partial action with a persisted deadline and SOC
ceiling. More distant partial actions await a fresh preceding-cycle commitment.
Whole-quarter future windows retain the normal two-window limits. Partial
schedule benefits are left unscored in the old whole-window comparison field.

The continuous server runs an independent five-second stop executor under the
same durable writer lease. An active short charge takes priority over a long
solve. Monitoring leaves room around the deadline. Stop on the timer, or sooner
if a post-start SOC sample no more than two minutes old reaches the ceiling.
OFF restoration takes priority over the deadline wait. A timer stop does not require fresh telemetry: ceasing charging remains possible
when telemetry or tariff evidence has expired.

The stop removes the active quarter while retaining later charge windows,
independently reads back the charge settings and records completion. Pending
intent is saved before transmission; failed or ambiguous stops are retried.
Completion masks that quarter during subsequent compilation, preventing an
ordinary replan from turning it back on. A natural quarter boundary limits
additional charging if the deadline executor or cloud is unavailable. Later
whole-charge windows can still execute during an outage, as with the existing
standard-window controller. API latency and stale cloud samples prevent exact
minute/energy guarantees; measured power is separate from settings confirmation.

## Periodic full charging

This policy addresses an operational maintenance requirement, separately from
arbitrage or insurance. AlphaESS describes regular full charging until charging
power falls to zero as **SOC calibration**, giving two weeks as an example in
its [residential manual](https://www.alphaess.com/Public/Uploads/uploadfile/files/20221114/4.Installation%26Operation%26MaintenanceManual_SMILE5-INV_S_V01.20221103-590.pdf).
The [AlphaESS application guidance](https://docs.alphaess.com/apps/alphaess-app/resi/advanced-settings.html)
also describes calibration for systems that have not fully charged/discharged
for a long time. This does not establish a universal LFP degradation rule or
replace the instructions for the installed battery/BMS.

When enabled, unknown history makes maintenance due. Otherwise the interval is
measured from the last observed full/taper criterion. The planner selects a
cheap feasible prefix fill within a persistent 24-hour deadline. If on-time
completion is no longer feasible, it chooses the earliest executable fill; it
cannot repeatedly renew a four-hour economic window. Slots blocked by the
instantaneous import limit are excluded. An insufficient priced or permitted
horizon remains pending rather than invalidating the ordinary economic plan.

At the completion quarter and for a further quarter, full charging permission
remains enabled for native taper/balancing observation. Early-stop charging does
not cut these completion windows short. Normal economic operation resumes
thereafter; the policy does not maintain 100% all day or force a discharge cycle.

A requested/forecast full charge never completes maintenance. Telemetry records
`last_full_soc_at` at >=99.9%. `last_calibration_at` additionally requires two
full-SOC samples 5–15 minutes apart with absolute battery power <=0.2 kW. This is
an observed full/taper criterion, not a BMS calibration acknowledgment or proof
of cell balance. Missing evidence leaves the task due. Optional native vendor
calibration must be coordinated with the sole controller to avoid two writers.

## Reproduction

### Physical commissioning

The explicit operator command `python -m src.manager.commission_early_stop
--owner-authorized` runs inside the existing service container with the owner's
authorization. It holds the same durable writer lease for the entire test;
ordinary planning and monitoring cannot become a second writer. The stop
executor borrows that lease and never releases it on the caller's behalf.

The test preserves a current charge, adds a bounded quarter after it, and
retains later charge windows. It refuses overlapping household-supply windows,
unverified tariffs, insufficient headroom or a configured import-limit conflict.
It records the deadline before dispatch, then calls the actual stop executor
after five minutes. Independent historical power samples must show significant
grid charging before the confirmed stop and two stopped samples no later than
the natural quarter end, with the first at least two minutes before that end.
This accommodates cloud samples exactly on five-minute boundaries while still
rejecting natural window expiry alone, full SOC, or charging that restarts.
Readback must complete within 90 seconds of the requested deadline;
a natural full-SOC stop, window expiry or acknowledgment alone cannot pass.

The result and sanitized relevant measurements are saved privately as
`early_stop_commission`. User OFF/revision changes cancel commissioning. Cleanup
restores the previous future settings while masking the stopped quarter. The
test itself does not activate any policy or persist `early_stop_verified`.
A recent completed stop can be rechecked against fresh device history without
another charging write, under the same lease and unchanged ON/revision intent.
The recorded deadline and confirmation must match the durable stop record;
failed or missing physical evidence still blocks certification.
Activation must separately verify the passed physical evidence, inverter
fingerprint and charging power, preserve the private configuration's ownership
and mount permissions, and confirm a fresh accepted cycle after restarting the
sole controller. A failed activation restores the prior configuration.

### Offline replay

```bash
uv run --frozen --extra emhass --extra dev pytest tests/ -q
uv run --frozen --extra emhass --extra dev python -m analysis.hedging_benchmark \
  --start 2026-10-12 --end 2026-10-18 --period 2026_recent --tag hedge_recent
uv run --frozen --extra emhass --extra dev python -m analysis.hedging_benchmark \
  --start 2025-11-10 --end 2025-11-16 --period 2025_autumn --tag hedge_autumn
```

The replay needs private measured/history/causal forecast caches described in
[the analysis guide](../analysis/README.md). Four paired policies isolate whole
quarters, partial charging, the additional hedge, and additional maintenance.
Use identical measured demand/PV, initial energy, tariffs and EV interlocks.
Report ending inventory, cash, losses and wear; simulated savings are not
realized savings. These inspected periods are not an untouched holdout.
