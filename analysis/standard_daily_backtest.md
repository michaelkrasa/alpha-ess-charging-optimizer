> Historical diagnostic: superseded by [the corrected controller validation](controller_v2_validation.md), including the observed charging rate and two charge/two discharge schedules.

# Standard daily API: household follow-up backtest

Evaluated 20 October 2026. Read-only household data collection and simulation; no inverter commands were issued. This evaluates the new `legacy_daily` charge-only planner, distinct from the previous two-window charge/discharge planner and from the daily timed-executor benchmark.

The weekly API is unavailable (6017), but both standard configuration read endpoints succeeded on this inverter. Existing charge/discharge windows and the 10% reserve were read without changing them. Discharge time control is still enabled on the appliance, which is incompatible with this new charge-only mode until commissioning.

## Interpretation update

[The no-battery and recorded-operation comparison](battery_value_comparison.md) separates storage value from management value. Recorded existing electricity cash cost is lower than the replacement simulation. These backtests have not established an improvement over actual existing operation; commissioning remains blocked.

## Results

Costs include variable invoice-based import fees, VAT, PV export revenue, assumed wear of €0.045 per discharged kWh, and common-price terminal-energy adjustment. Current tariff coefficients and assumed historical VT 09:00–13:00 are a scenario, not a historical invoice reconstruction.

| Period | Native economic cost | Standard daily economic cost | Benefit vs native | Native / daily import-limit exceedances |
|---|---:|---:|---:|---:|
| 2025 · 345 usable days | €1610.90 | €1592.12 | €18.78 | 1 / 3 |
| 8 Sep–19 Oct 2026 · 42 days | €224.69 | €216.61 | €8.08 | 0 / 0 |

Both policies have zero simulated reserve violations. In the 2025 case the daily fallback saves €40.51 in electricity cash cost, offset by €21.77 of additional assumed wear and a small terminal-energy difference. These benefits are modest and do not establish measured savings or justify assuming successful hardware execution.

The earlier [rolling-controller report](household_backtest.md) found €38.74 and €19.51 benefits versus native on these same two windows. Its fixed-daily benchmark still needed a quarter-hour executor. The actual standard-API fallback tested here has lower modeled benefits; the earlier small daily-versus-rolling difference must not be reused as its performance estimate.

## Policy and simulation

- Candidate search selects up to two nonoverlapping daily charging periods from cheap forecast windows, with native PV capture and household self-consumption otherwise. Durations are 15 minutes through four hours; three cheapest candidates per duration are considered, including nonoverlapping pairs. This is a bounded heuristic, not proof of a global optimum.
- Charging uses the configured maximum SOC (100% here); duration controls how much to buy. We do not assume a lower `batHighCap` affects grid charging alone while leaving PV capture unlimited.
- The API has no power setpoint. The simulation assumes appliance charge power is already configured to 5 kW. No real-time import-power clipping is invented. Forecast candidates that add an import overload are rejected.
- One update opportunity is evaluated after 15:00 Europe/Prague, using only published prices and archived forecasts available then. Changes are separated by at least 24 hours; identical settings consume no write. Selected schedules take effect from the next quarter-hour and repeat daily until changed, including when a later forecast is unavailable.
- Initial charge, continuous battery energy, segment boundaries, elapsed-time integration, forecast calibration and data exclusions match the principal backtest. The year contains eight segments; the recent period is one. The archived IFS variant differs from the deployed automatic weather-model choice.
- Daily endpoint times cannot cross midnight or exceed 23:45; that final quarter-hour stays native. Plans crossing a DST change are rejected. API latency, outages, physical device behavior and manual intervention are not replayed; exact write-time drift is not modeled. A prolonged forecast failure may trigger a separately budgeted native-restoration attempt in production; the comparison conservatively retains the repeating schedule instead.
- There is no battery-to-grid export or controllable EV model. As in the main report, the AlphaESS household-load measurement boundary cannot independently establish which historical demand was ESS-suppliable.

## Hardware and OFF limitations

Two additional forecast-import-limit exceedance intervals arose from unexpected household demand during charging in the annual simulation (three versus one under native). No such intervals occurred recently. This is a concrete reason to verify appliance-side import protection and configured power before commissioning: software cannot enforce live power limits through this endpoint. The 20 kW planning limit is not a verified breaker-trip threshold.

The adapter reads settings before each change, writes the complete charge configuration, and reads it back. It never silently substitutes periodic control or changes discharge settings. A durable 24-hour budget is reserved before a request, with its final timestamp recorded immediately before transmission; timeouts retain that budget across restarts. OFF uses the same endpoint and budget. If blocked, OFF remains pending and the existing inverter schedule may continue. Use the AlphaESS app for an urgent manual change; access to that app is not proof of immediate physical execution either.

`device_control_verified` remains false. Commissioning needs native household self-consumption with discharge time control disabled, reserve 10%, known configured charge power, and appliance import protection. The previous schedules remain unchanged. No live commands were sent or AWS resources deployed.

## Reproduce

```bash
uv run python analysis/backtest_household.py --forecast weather --policies native standard_daily --output analysis/output/backtest_2025_standard_daily
uv run python analysis/backtest_household.py --start 2026-09-08 --end 2026-10-19 --forecast weather --policies native standard_daily --output analysis/output/backtest_2026_standard_daily
uv run pytest tests/test_daily.py -q
```

Uses the same local ignored household and archived forecast caches described in the [main report](household_backtest.md). Detailed totals and daily costs are in `analysis/output/backtest_2025_standard_daily.{json,csv}` and `analysis/output/backtest_2026_standard_daily.{json,csv}`. A zero generic fallback counter in these files is not proof of forecast availability; that metric belongs to the rolling policy.

API constraints: [AlphaESS library API reference](https://github.com/CharlesGillanders/alphaess-openAPI/blob/main/docs/API.md#charge--discharge-configuration). Forecast/source qualifications: [methodology audit](backtest_methodology.md).
