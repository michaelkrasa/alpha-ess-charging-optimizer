> Historical diagnostic: superseded by [the corrected controller validation](controller_v2_validation.md), including the observed charging rate and two charge/two discharge schedules.

# Battery value, management value and observed operation

Checked 20 October 2026. The €18.78 result is the extra modeled economic benefit of the new standard daily manager over native battery self-consumption, on 345 evaluated days. It is not the value of owning the battery. All comparisons keep the household solar panels and measured load unchanged.

| 345 evaluated 2025 days | Variable electricity cash cost | Economic cost including assumed wear and ending-energy adjustment |
|---|---:|---:|
| Solar, no battery | €1,780.90 | €1,780.90 |
| Solar, native battery | €1,489.34 | €1,610.90 |
| Solar, new daily manager | €1,448.83 | €1,592.12 |
| Recorded existing grid flows | €1,362.67 | Not estimated |

Native storage therefore reduces modeled electricity spending by €291.56, or €170.00 after the wear/energy adjustment. Daily management adds €18.78. Battery purchase price, subsidies, financing, backup value and fixed standing charges are excluded; this is not a payback calculation.

## Why the new manager is not yet verified

The recorded-grid-flow comparison is a diagnostic, not a controlled trial or an invoice reconstruction. Historical settings, initial battery condition, availability, actual charging power, efficiency and load measurement boundaries differ from the model. Recorded wear has not been estimated here. Nevertheless, existing recorded electricity cash cost is lower than the new daily simulation by €86.16 on the annual window and €40.58 on the recent window. We have not established that the replacement improves the actual existing operation. The earlier previous-planner comparison replayed its algorithm with common forecasts, rather than exactly reproducing its historical actions.

The new code passes 442 tests (131 skipped), and backtests isolate future observations from decision inputs. These checks establish software/model properties, not correct inverter execution. The 5 kW appliance charging-power assumption and import protection have not been verified. The standard daily simulation added two import-planning-limit exceedances in the annual window. Native self-consumption commissioning also requires disabling the currently enabled discharge time control. Device writes remain blocked, and no daily write attempt is stored.

At the runtime check the service was a Python process on the user’s Mac, bound to 127.0.0.1:8765. It was launched as a local process and needs the computer awake and the process alive. AWS has not been deployed. The dashboard’s saved ON request does not bypass the commissioning blocks; existing inverter settings continue operating.

## Calculation

For each included quarter hour with measured household load L and solar PV S, no-battery import is max(L−S,0)×0.25 kWh, and export is max(S−L,0)×0.25 kWh. Multiply these by the same settlement-day import/export prices used by the backtest. Observed cash cost uses the corresponding measured grid import/export channels instead. No battery forecast or optimization enters the no-battery baseline. Included days are read from the saved standard-daily result CSV files, ensuring identical coverage.

The current 2026 tariff and assumed historical VT 09:00–13:00 are used consistently. The results cover 345 usable days, not a complete observed year. Snapshot integration, excluded days and unverified measurement boundaries remain material limitations. Detailed totals are in [the comparison JSON](output/no_battery_comparison.json); physical and tariff qualifications are in [the standard daily report](standard_daily_backtest.md) and [the main report](household_backtest.md).
