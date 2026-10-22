> Superseded operating assumption: [21 October live repeated-write test](rapid_write_verification.md) demonstrated that this household does not enforce the documented 24-hour write restriction. Live control now replans every 15 minutes. Daily-only financial results below remain historical diagnostics.

# Validation and review — 21 October 2026

**Verdict: software review corrections pass; live commissioning and financial acceptance do not yet pass.** The local dashboard is running with fresh telemetry and a projection rebased to measured SOC. Automated inverter writes remain disabled. No inverter settings were changed or API write budgets consumed during this review.

Scope: the current working tree against HEAD `6280209`, including untracked manager code. The specification is the household requirements in this conversation: EUR, no EV, 10% reserve, no intentional battery export, profitable overnight charging up to 100%, executable legacy API schedules, dashboard ownership/restoration and causal household backtests. No applicable repository AGENTS.md or code standards were found. AWS and Synology deployment remain deferred by request.

## Standards review

The independent standards review found two actuator safety defects, both corrected:

1. A cycle could outlive its 240-second lease and dispatch after another process acquired ownership. SQLite and Dynamo now check current ownership, and transmission guards require at least 30 seconds of remaining lease. Ownership is also checked after network awaits, before state updates, and during fallback restoration. Regression tests inject lease expiry and successor ownership, including failures during readback. An already pending vendor request cannot be revoked after a timeout.
2. Periodic writes checked cancellation before a rate-limit delay, rather than immediately before transport. The control/lease guard now reaches the final transmission call. Regression tests cancel during the delay. The household uses the legacy backend, but the supported periodic path has the same protection.

A read-only API6006 timestamp error receives at most one spaced retry through the SDK, generating a new signature. Writes and other errors never retry. This addresses recovery from a transient rejection; it does not claim the overnight root cause was proven. Fresh calls succeeded during this session.

Standards total: two safety findings fixed; no remaining blocking source-level finding from that axis. Syntax/undefined-name checks, compilation and diff whitespace checks pass. A full style-lint cleanup was not a validation criterion.

## Specification review

The independent specification review found three operational defects:

1. Final charge/discharge settings could be valid while a failed second write left an overlapping intermediate pair. The adapter now checks both possible write orders against the current settings, chooses a safe intermediate order, and rejects transitions for which neither order is safe. Tests inject second-write failure in both orders and verify OFF and unchanged-endpoint behavior.
2. An ordinary dark day could invalidate a usable PV calibration and stop planning. Correctly aligned, finite positive calibrations now remain usable for up to seven days; their original timestamp is preserved. Missing or older calibration still blocks new forecasts. Tests cover two-, seven- and eight-day-old calibration.
3. Quarter-hour-only polling could miss a daily budget expiry and progressively delay updates. The local loop now wakes at an upcoming budget deadline between monitoring ticks; stale/malformed deadlines cannot cause a hot loop. Network duration can still add delay. Fixed-time replay sensitivities below quantify a limited timing effect; they do not reproduce endpoint failures or exact transmission jitter.

Two additional issues were caught during live dashboard validation and corrected: a saved plan's remaining SOC projection is now recomputed from current measured SOC rather than yesterday's hypothetical state; and daily forecasts are consistently labeled proposals rather than using an unrelated previous acknowledgment as proof of execution. Saved settings are retained before price publication, not reoptimized from unpublished prices. Stale economic comparison figures are cleared, and expired plan intervals are excluded.

Specification total: three independent operational findings addressed, plus two dashboard/forecast corrections. Financial superiority and physical execution remain acceptance gaps; they are not source-review passes.

## Verification results

- 507 tests passed; 131 skips belong to existing legacy optimizer parameter cases. New manager regression tests were exercised.
- `ruff check src/manager --select E9,F` passes. Python compilation and `git diff --check` pass.
- Live HTTP checks: unauthenticated state401, wrong password401, correct dashboard login200, authenticated state200. No real ON/OFF mutation was needed; control behavior is covered using device fakes.
- Browser inspection confirms fresh measured SOC around53%, first remaining projected SOC around52%, proposed-schedule labeling, EUR prices, and the explicit blocked-device state. Evidence: `../output/dashboard-validation-2026-10-21.png`.
- The local process runs `uv run python main.py serve --live` on the Mac. `--live` permits device access, but the commissioning setting remains false and blocks mutations. This is not a persistent deployment or autostart service.

## Household observations

Fresh read-only configuration calls succeeded. Charge controls remain enabled at100%,12:30–14:15 and21:30–22:00; discharge time control remains enabled with10% cutoff,06:15–10:30 and17:15–21:30. Earlier reads had06:30 morning start, so old configuration cannot be assumed constant through all historical data.

Using20 October's288 samples and21 October's partial history:

| Observation | Evidence | Interpretation |
|---|---|---|
| Night charging | Six samples average about8.47kW inferred battery input | Supports the corrected8.6kW charging assumption |
| Energy preservation |93 overnight samples, SOC75.6% throughout | Supports holding outside discharge windows |
| Household supply | SOC falls74%→54% over32 morning samples | Supports discharge during configured windows |
| Full charge | SOC reaches100% during yesterday's charge window | Full charging works under the existing configuration |
| Full-SOC residual | Roughly0.64kWh positive balance residual in selected full-SOC samples | Minor model limitation, deferred at the household’s request; not a commissioning blocker or an independent battery-energy measurement |

The sample-based diagnostic and input hashes are in `output/device_observation_validation.json`. This was passive validation of existing settings, not a controlled write-and-observe experiment. The API documentation describes `batUseCap` as a discharge cutoff, but acknowledgment and settings alone do not establish physical enforcement: [upstream API reference](https://github.com/CharlesGillanders/alphaess-openAPI/blob/main/docs/API.md).

A10% depletion cutoff was not reached in the fresh observation window. Older data contains SOC below10% under unknown historical configurations. Per-phase import protection was not established. New schedule permission, readback and physical application have not been tested with a controlled write. These are concrete missing commissioning evidence, not reasons to set a verification flag automatically.

## Financial validation

See [the corrected backtest](controller_v2_validation.md) for causal forecasts, coverage, tariffs, battery assumptions and exclusions. The existing default actions and financial policy were not tuned using the new timing results.

| Recent42-day case | Replacement economic cost | Original economic cost | Replacement saving |
|---|---:|---:|---:|
|15:00 decisions |€208.07 |€206.89 |−€1.18 |
|15:15 sensitivity |€207.94 |€206.89 |−€1.05 |
|16:00 sensitivity |€208.75 |€206.89 |−€1.86 |

The replacement still exceeds its20kW planning limit in four recent simulated intervals, peak21.10kW. The legacy API has no modeled active power clamp. The345-day case saves€52.50 against the old algorithm and the separate18-day July check saves€5.17, including assumed wear and terminal adjustment. None proves actual household bill improvement, particularly given differing historical reserve behavior.

**Release decision:** keep the new controller in observation/planning mode. Calling the exercise fully commissioned or claiming guaranteed savings would contradict the evidence. Completing live commissioning requires an observed controlled schedule cycle, confirmed10% cutoff and import protection, followed by measured bill/energy reconciliation. No additional permission request is being invented; those checks are simply not complete and this report records the failed acceptance verdict.
