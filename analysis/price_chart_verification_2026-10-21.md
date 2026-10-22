# Price chart and household schedule verification — 21 October 2026

> Subsequent owner instruction: the unverified 20 kW planning assumption has been removed from household configuration and from the settings default. The headroom discussion below records the diagnosis before that change; it no longer describes a live charging restriction.

The dashboard price scale was linear, but the presentation obscured the day's two peaks. It showed only future plan intervals, so the morning disappeared at noon; it plotted the household import tariff rather than the exchange price; and its vertical axis always started at zero. The complete market-price day was already cached but was not exposed to the dashboard independently of planning.

## ENTSO-E cross-check

The Czech bidding-zone `10YCZ-CEPS-----N` day-ahead table for 21 October was read from the public [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/market/energyPrices?appState=%7B%22sa%22%3A%5B%22BZN%7C10YCZ-CEPS-----N%22%5D%2C%22st%22%3A%22BZN%22%2C%22mm%22%3Atrue%2C%22ma%22%3Afalse%2C%22sp%22%3A%22HALF%22%2C%22dt%22%3A%22TABLE%22%2C%22df%22%3A%222026-10-21%22%2C%22tz%22%3A%22CET%22%7D). It contains 96 quarter-hour EUR/MWh prices. All 96 match the manager's cached OTE prices exactly; maximum absolute difference is **€0.00/MWh**. Local time is CEST, UTC+02, on this date. Midnight corresponds to 20 October 22:00 UTC.

Lowest market price: €143.37/MWh at 23:45. Highest: €282.72/MWh at 16:45. Spread: €139.35/MWh. Morning prices reach €278.92/MWh at 08:00. The market is €159.87/MWh at 05:00 and €170.34/MWh at 13:45.

The dashboard's **import** prices include variable supplier/distribution/system/tax fees and VAT. They therefore differ from the exchange's wholesale prices by design. At the cached settlement FX estimate, today's import range is €208.25–€397.57/MWh, with the maximum at 09:00 because VT distribution pricing also applies then. This distinction is now selectable and explicit.

Machine-readable, timestamped interval comparison: `output/entsoe_price_verification_2026-10-21.json`. The original displayed table is retained privately under `data/commissioning/entsoe_2026-10-21_table.txt`.

## Dashboard correction

- Full cached today/tomorrow market days are exposed separately from future-only battery plans. Morning prices remain visible after the morning has passed.
- Default **Market spot** view is directly comparable with ENTSO-E/OTE. **Your import** shows the household tariff using the same pricing function as the planner.
- Price is a quarter-hour step plot on an explicitly linear axis that fits the shown price range. The axis need not start at zero; negative prices remain supported. Exact low, high and spread values are displayed.
- SOC remains on a separate 0–100% axis and is drawn only where a future projection exists. Past SOC is not invented. Shading and a Now marker distinguish elapsed time; midnight remains a day boundary.
- Incomplete market days are not fabricated. Market prices can still be shown without valid FX; the corresponding unknown import prices remain unavailable.

The service was restarted to load the dashboard/backend changes. Automatic control remains enabled, and the subsequent cycle was active with a fresh configuration readback. No planner parameters or live import-headroom constraints were changed. The browser verified market/import switching, full-day prices, range labels and the corrected chart visually. Screenshot: `../output/dashboard-full-day-prices.png`.

Regression loop: `uv run pytest tests/test_price_outlook.py -q` initially failed twice because full-day prices were absent from `/api/state`; both cases pass after the fix. The actual JavaScript canvas renderer is also exercised through Node: it asserts the morning survives at noon, the price spread uses more than 80% of chart height, equal price differences have equal vertical distances, negative/flat values work, and historical SOC is not fabricated. Full suite: **522 passed, 131 skipped**, one existing Starlette deprecation warning. JavaScript syntax, static and whitespace checks pass.

## Does today's two-cycle argument hold?

The shape does offer morning and evening opportunities. The manager's actual installed household supply windows include **05:00–11:00** and **16:00–23:45**. A configuration read is not evidence that the same settings were installed for the entire past day, so actual household measurements were checked separately.

The first measured SOC at 00:03:50 was **75.6%**. During 06:15–10:15, measured household consumption was **4.10 kWh**, grid import was **0.157 kWh**, and the minimum measured SOC was **49.2%**. Thus the morning peak was largely served from stored energy and solar already; an empty battery was not forcing expensive morning imports. These energy figures use elapsed-time integration of timestamped five-minute samples, rather than adding power readings.

A reconstructed midnight plan using only prior completed load history, a weather run available with a six-hour release lag, known prices and the initial SOC does select **05:00–05:30 charging**. That is a forecast counterfactual, not an executed command or proof of today's realized saving. The new rapid-replanning behavior was enabled later in the morning and cannot retroactively execute this window.

A diagnostic family of 14,251 morning-plus-midday charge pairs with two peak-covering discharge windows was evaluated using the same physics. The best admissible member costs approximately €0.09 more in the central forecast objective than the reconstructed one-charge plan. This limited family is not a proof of the global optimum, and a forecast result cannot settle the realized full-day comparison while the day is unfinished.

The **13:45–14:00** valley is currently screened out by the charging-headroom envelope: recent peak load **13.09 kW** plus fixed charging **8.6 kW** gives **21.69 kW**, above the **20 kW** planning limit. Current expected load alone is lower, but the separate historical envelope is deliberately conservative. In the saved noon forecast, the best tested future charge candidate with the envelope costs about €0.01 more than the installed plan in the central-capacity scenario (about €0.02 across the planning capacity scenarios). Ignoring that screen makes 13:45–14:00 charging about **€0.06 cheaper** in the central forecast objective. That is a diagnostic of a foregone forecast opportunity, not authorization or justification to disable the live constraint. No dynamic import clipping or variable charging-power control has been assumed.

The day-specific inputs were frozen under ignored `data/commissioning/price_day_snapshot_2026-10-21.json`; measured samples are in `power_2026-10-21.json`. Reproduce the diagnostic with `uv run python analysis/diagnose_price_day.py`. Public result summary: `output/price_day_diagnosis_2026-10-21.json`. It contains assumptions and separates observed morning data from forecast comparisons.
