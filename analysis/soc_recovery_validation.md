# Below-reserve telemetry tolerance

Normal ESS balancing can report SOC below the requested reserve. That reading is not itself a battery fault. The controller must retain the measured state and continue producing executable plans.

## Reproduction and correction

A synthetic 16-quarter solve beginning at 8% SOC with a 10% reserve reproduced `Infeasible`. The adapter applied a negative available-discharge upper bound even when household-discharge mode was off, making zero discharge impossible.

The bound is now conditioned on the household-discharge mode. With the mode enabled, the original reserve bound remains exact. With it disabled, discharge remains zero and the irrelevant negative bound is relaxed using the existing physical big-M constant. No extra binary variables, reserve reduction, SOC clamping, objective changes or invented energy are introduced. Native PV capture and charging use the unchanged physical replay. Recovery timing remains an economic decision; below-reserve readings do not automatically force immediate grid charging.

## Evidence

- The original reproduction failed before the correction and passes afterwards.
- Actual EMHASS solves cover 0%, 8% and 9.9% starting SOC, including slow 1 kW recovery, with cold/cached solves. Further discharge below reserve is forbidden; measured starting SOC is preserved.
- The complete local suite passes 614 tests (131 skipped).
- Four read-only 101-quarter replays from captured private forecasts pass at starting SOC 8% and 10%, cold/cached, with independent executable-physics replay and unchanged solver limits.
- The guarded ARM64 deployment passed target-host acceptance: live management remained enabled, measured 8% SOC was retained, the full 101-quarter horizon solved optimally in 37.057 seconds (0.0655% MIP gap), and device settings matched readback. The following scheduled cycle also passed: 100 remaining quarters, 42.074 seconds, 0.0829% gap. Both ran within the unchanged 60-second / 0.1% solver limits. Private deployment and API evidence is retained under ignored `output/`.

Household inputs and operational reports stay under ignored `output/`. This corrects input tolerance, not measured profitability or physical SOC behavior.
