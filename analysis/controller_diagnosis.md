> Historical diagnostic: superseded by [the corrected controller validation](controller_v2_validation.md), including the observed charging rate and two charge/two discharge schedules.

# Why the daily fallback underperformed: controlled forecast diagnostic

Investigated 20 October 2026. This is a read-only diagnostic, not a production forecast change, hardware test or independent holdout validation. The reported results do not establish that an active controller was running on the battery.

## Findings

1. **Observation frequency is not command frequency.** The service reads SOC and updates a proposed plan every 15 minutes, but the standard configuration endpoints are documented as writable once per 24 hours. It cannot freely execute a different charge/hold/discharge decision at every observation.
2. **The replacement has fewer implemented controls.** The previous planner uses both charge windows and discharge time-control windows. The new `legacy_daily` fallback only programs charge windows and assumes native household self-consumption at other times. Its modeled battery can therefore be used at lower-value times rather than reserved for a later peak. The older API still exposes two discharge-control windows; omitting that control was an implementation simplification, not a limitation implied by code 6017. Its actual physical semantics still require verification.
3. **The load estimator is badly mismatched to expected energy in the modeled channel.** Seven-day same-time medians suppress intermittent large loads. They are useful for median/MAE prediction, but minimizing expected energy cost needs an expected-energy forecast. Aggregate forecast energy is 36–37% below the AlphaESS load channel used by the model.
4. **The comparison with recorded operation is not controlled.** It prices measured grid flows against a counterfactual battery simulation whose physical power/efficiency and available-load assumptions are unverified. The old-policy replay is also not an exact replay of historical actions. It is premature to infer that a more capable controller is intrinsically worse, or that the replacement has demonstrated an improvement.

## Hypotheses and probe

Ranked hypotheses were (1) underpredicted load leads to too little cheap charging, (2) omitted discharge time control loses useful peak preservation, and (3) unverified physical/measurement assumptions confound the observed-cost comparison. Only the first hypothesis was changed in this probe. The production service and inverter settings were left unchanged.

The ablation replaces the seven-day raw-sample quarter-hour median with the mean of the previous seven completed days’ elapsed-time-integrated quarter-hour load. Prices, weather forecasts, battery parameters, candidate windows, wear, initial energy and scoring remain identical. Neither variant uses future actual consumption for planning. This is a diagnostic use of already evaluated data, not a newly untouched test set.

| Evaluation | Median energy bias | Mean energy bias | Daily cost: median | Daily cost: mean | Improvement from mean |
|---|---:|---:|---:|---:|---:|
| 2025 · 345 usable days | -36.95% | +0.07% | €1592.12 | €1583.19 | €8.93 |
| Recent · 42 days | -35.91% | +0.23% | €216.61 | €210.08 | €6.53 |

Bias is the sum of causal completed-day seven-day profiles compared with actual daily energy. It is a clear diagnostic of the estimator, not exact aggregation of every rolling intraday forecast. Economic cost includes assumed battery wear and the same terminal-energy adjustment.

Changing this one estimator improves the new daily policy in both evaluated periods. It does **not** close the recent gap to the legacy replay: mean-load daily costs €210.08, versus €204.09 for the legacy replay on those 42 days. The legacy replay itself stays unchanged because it uses its original fixed consumption assumption; it does not consume the new load profile. The full-year mean-load daily result is €1,583.19, versus €1,632.75 for the legacy replay under this common simulation. Thus even the direction of the old/new ranking varies by period.

This supports the underforecasting hypothesis as a partial explanation; it does not quantify the separate contribution of omitted discharge control or prove every remaining discrepancy is a software bug. No hourly-future-load oracle, tuned multiplier, artificial EV removal or hindsight parameter selection was used.

## Household-load boundary still matters

The user has explicitly excluded EV control and states the EV cannot be supplied by the ESS. A separate measured non-EV/ESS-suppliable channel is not established in this archive. Some intermittent demand suppressed by the median may be outside the controllable ESS load boundary. Therefore do not blindly promote the mean ablation into live operation or interpret all measured household load as battery-suppliable. The model, forecasts and physical measurement boundary must be reconciled together.

## Why drain-then-refill is not always the optimum

Ending the expensive period near the 10% reserve before a cheaper night can be sensible if household demand can consume the energy. This ESS cannot intentionally export its battery; it cannot force the target by selling excess energy. A full night charge may crowd out free daytime PV. Published day-ahead prices are known only through the next published day, while future solar and demand remain forecasts. With 95% one-way efficiencies, one delivered battery kWh requires about 1.108 grid kWh. Buying at €0.10/kWh and avoiding €0.15/kWh gives about €0.156/kWh including the assumed €0.045 discharge wear, so the cycle is not profitable under those assumptions. This example uses all-in marginal prices, not raw spot prices.

With the same feasible actions and objective, an exact optimizer that includes the old policy as a candidate cannot score worse than that candidate on its own forecast. That guarantee does not apply to the current bounded charge-only candidate search, to inaccurate forecasts, or to realized future outcomes. The sound next implementation is a like-for-like charge/discharge policy with the existing planner retained as an explicit candidate/fallback, followed by matched replay and physical commissioning. More frequent polling alone is not a solution.

## Reproduce

```bash
uv run python analysis/diagnose_load_bias.py --output analysis/output/load_bias_recent
uv run python analysis/diagnose_load_bias.py --start 2025-01-08 --end 2025-12-31 --output analysis/output/load_bias_2025
```

Detailed outputs: [annual diagnostic](output/load_bias_2025.json), [recent diagnostic](output/load_bias_recent.json). Related: [storage versus no-storage comparison](battery_value_comparison.md), [standard API backtest](standard_daily_backtest.md), [NAS deployment](../NAS.md).
