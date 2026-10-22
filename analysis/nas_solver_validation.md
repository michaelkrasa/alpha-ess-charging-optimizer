# Full-horizon solver validation

NAS acceptance completed — 22 October 2026.

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

All six checks passed on the actual ARM64 NAS, in a network-isolated container using the installation settings and captured 131-quarter input:

| Starting SOC | Cold solve | Cached solve | Physical replay |
| --- | --- | --- | --- |
| 60.8% | 24.346 s | 22.851 s | Passed both |
| 61.6% | 41.570 s | 0.905 s | Passed both |
| 62.4% | 15.113 s | 7.393 s | Passed both |

All achieved objective gaps were below 0.1%. The tested image was then deployed with the existing private configuration, credentials and enabled control intent preserved.

The first live cycle solved **all 128 available future price intervals in 19.899 seconds**, with a 0.09847% objective gap. The returned plan timestamps were compared with every published price interval from the next boundary onward: no intervals were dropped. Both inverter configurations were accepted and confirmed by readback at **15:53:54 CEST**. Controller health became `active`, management remained ON, and cloud contact was fresh. The guarded updater reported success; this is execution of the deployed service, not only an isolated benchmark.

Runtime image config identity: `sha256:210772448b86a13a581c07629acbdd846f33bf3c0b7358c94fc609d7d7a62651`. Raw status and deployment logs are retained privately under ignored `output/`.

Settings readback establishes stored configuration, not guaranteed execution of future intervals or realized financial savings.

Raw captured inputs and operational reports stay outside Git. Exact timings are machine/load/input dependent; these checks do not prove that every future MILP will finish within the limit.

## Dashboard and one-command update acceptance

A subsequent update on 22 October verified the restricted, root-owned deployment helper on DSM Python 3.8 without `visudo`. Its harmless probe runs with root only through the fixed no-argument sudo command; general passwordless commands remain unavailable. The normal `./scripts/deploy-nas.sh` path then completed unattended, preserving live ON intent, checking the exact runtime image identity and requiring a fresh post-restart controller cycle.

The updated service solved the complete 123-quarter horizon in 48.026 seconds, status `Optimal`, achieved objective gap 0.03440% against the configured 0.1% limit. Both charge and household-supply settings matched readback. The older-planner comparison also completed after correcting its missing packaged configuration dependency. Daily energy counters and prospective SOC tracking were populated; served dashboard bytes matched the locally tested source. The local suite passed 610 tests (131 skipped). No objective, tariff or battery-physics assumptions changed in this dashboard/deployment update.
