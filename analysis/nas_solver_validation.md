# Full-horizon solver validation

Validation in progress — 22 October 2026.

## Cause and change

After next-day prices published, the priced horizon expanded beyond 96 quarter hours. The adapter asked HiGHS to prove a 0.01% relative objective gap within 15 seconds. A low-power ARM NAS returned `User_Limit`; faster development-host solves did not establish NAS reliability.

The adapter now allows 60 seconds and a 0.1% relative objective gap (upstream EMHASS defaults to 1%). This is an optimization certificate tolerance, not a percentage of savings or a relaxation of physical constraints. The complete published-price horizon is retained. `Optimal` status is still required; every executable decision is independently replayed against battery physics and rejected on SOC mismatch.

The controller records interval count, solve duration, status, achieved gap and configured bounds under `planner_comparison.emhass_solver`. The read-only `src.manager.solver_check` command exercises cold and cached solves without API credentials or device writes.

## Local evidence

- Suite: 587 passed, 131 skipped, including a synthetic 131-quarter regression and physical replay.
- Captured private 131-quarter NAS inputs: three starting SOC values (60.8%, 61.6%, 62.4%), cold/cached solves all passed; achieved gap below 0.1%.
- Original image under a 0.1-CPU Docker quota reproduced `User_Limit`.
- Updated Linux ARM64 image under the same quota passed all 131 intervals: cold 36.297 seconds / gap 0.09824%; cached 32.907 seconds / gap 0.09982%.
- No battery capacity, power, tariff, demand or solar assumptions changed. No horizon truncation, fallback to the old planner, or relaxed-plan acceptance was introduced.

## NAS acceptance

Pending actual hardware execution. The staged root command benchmarks six full-horizon runs in an isolated container, then updates the service only after they pass. Acceptance also requires a fresh live controller cycle with a full-horizon certificate, cloud contact and confirmed inverter charge/discharge settings. HTTP availability alone does not pass.

Raw captured inputs and operational reports stay outside Git. Exact timings are machine/load/input dependent; these checks do not prove that every future MILP will finish within the limit.
