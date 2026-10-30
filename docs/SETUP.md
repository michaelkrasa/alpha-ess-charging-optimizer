# Setup and commissioning

## Observation first

Use Python 3.12–3.13 with `uv`, or the [Docker guide](../NAS.md). The tracked configuration and examples are conservative placeholders. Keep installation-specific settings in the ignored `*.local.yaml` files.

```bash
cp .env.example .env
cp examples/manager.yaml manager.local.yaml
cp examples/solar_config.yaml solar.local.yaml
chmod 600 .env
uv sync --frozen --extra emhass --extra dev
uv run --extra emhass python main.py --config manager.local.yaml serve --host 127.0.0.1
```

Fill `APP_ID`, `APP_SECRET` and `SERIAL_NUMBER` using your AlphaESS OpenAPI credentials. Enter panel coordinates, tilt and azimuth in `solar.local.yaml`. Adjust battery power, capacity, reserve, efficiency and wear assumptions in `manager.local.yaml`. Example values are placeholders, not inferred hardware capabilities.

`main.py` is the entry point. Start with loopback binding; use `--host 0.0.0.0` only for a trusted LAN. The dashboard has no local password.

## Prices, tariffs and forecasts

`price_source: ote` uses Czech day-ahead prices. `price_source: entsoe` uses `ENTSOE_TOKEN` and `entsoe_area`. Both are expressed in EUR. The continuous server checks its plan on five-minute clock boundaries. Price and inverter decisions still align to 15-minute interval timestamps; write spacing, readiness checks and readback remain in force.

The current tariff adapter is Czech and contract-specific (`src/manager/tariff.py`). ENTSO-E prices alone do not include import taxes, network fees, VAT or export terms. Verify those before using another contract or country. Time handling includes Prague assumptions; general timezone support is roadmap work.

Configure verified high-tariff periods and, optionally, their start date. Missing periods or a future start date block new live schedules. Periods do not expire automatically: review them when the distributor changes the HDO schedule. The removed `tariff_valid_until` setting in older configurations is ignored. Fixed standing charges do not belong in marginal charging decisions.

Household demand uses recent measured history. Solar combines Open-Meteo tilted irradiance and calibration against measured production. An installation without adequate history/calibration may be unable to plan initially; inspect the error instead of inventing zero consumption or solar. `exclude_inferred_grid_only_ev` is opt-in: it infers sessions for a particular grid-only EV topology and is not a universal EV detector.

## Before live control

- Confirm supported AlphaESS API operations and actual charge/discharge behavior. Weekly scheduling permissions vary; `6017` means that feature is unavailable.
- Verify battery power/capacity, reserve, billing formula, tariff times, forecast coverage and device timestamps.
- Review an executable plan in observation mode and compare it with measurements.
- Set `device_control_verified: true` only after commissioning. Periodic mode also requires verified hold behavior.
- Stop any other live controller before enabling this one.

Then start with `--live` and switch management ON:

```bash
uv run --extra emhass python main.py --config manager.local.yaml serve --live --host 127.0.0.1
```

A fresh database starts OFF. `--live` permits writes; the persisted switch controls whether automatic management requests them. OFF requests native solar/self-consumption restoration; pending restoration remains visible until confirmed.

Readback proves stored settings matched. Measured power proves activity only at its timestamp. Neither is a guarantee of future execution or savings.

## Operational limits

Standard daily windows are rewritten during the day, but remain on the inverter during cloud outages. Charge/discharge writes are separate API calls, so partial changes need explicit handling. The API's 23:45 endpoint cannot express the final quarter as an ordinary same-day window. Ambiguous daylight-saving horizons are rejected. Forced battery export and direct LAN/Modbus control are not implemented.

The optimizer uses published prices only and can charge to 100% when justified after losses, wear and expected solar. It plans over the available horizon, which can extend into tomorrow. Solver success and speed must be checked on the actual host; Mac performance is not proof of NAS performance.

Optional [forecast-risk buffers, partial charging and periodic full charging](HEDGING.md)
retain the standard-window EMHASS planner. Five-minute charging needs the
continuous server's deadline executor and separate stop/readback/measured-power
commissioning (`early_stop_verified`). One-shot and Lambda control cannot use
that mode. The configurable full-charge interval addresses observed SOC/taper
maintenance; it does not assert a universal LFP degradation rate. All three
policies are disabled by default in the example configuration.

## Development preview

Use the local HTML with read-only state from an existing manager:

```bash
uv run --extra emhass python scripts/preview-dashboard.py --source http://YOUR_SERVER:8765
```

Open `http://127.0.0.1:8766`. POST requests are rejected, leaving the existing manager as the sole writer. This is a UI preview, not an independent live controller.

## Battery forecast tracking

The price chart overlays expected and measured battery charge on a 0–100% axis. Measurements are aligned to quarter-hour boundaries; short, bracketed gaps are interpolated, longer gaps remain blank. Curves are smoothed for display without changing the recorded points. Hover or use the arrow keys to inspect the time, prices, SOC and actual-minus-expected difference in percentage points.

After a confirmed active plan, future SOC expectations are saved and may update until their interval begins. Past expectations are retained unchanged. A new installation therefore shows measured history immediately when available, but cannot invent historical predictions; comparisons accumulate prospectively. Deltas reflect forecast and execution differences, including manual intervention. Day summaries describe the current plan and import-price peaks; they do not promise savings.

The solar strip in Forecast detail shares the price chart’s exact time axis and shows forecast production beside measurements. The expanded meter details show daily generation and total battery charging from the AlphaESS daily-energy counters, refreshed about every 15 minutes. Battery charging includes solar and grid; it is not labelled as solar-only charging. Unavailable totals remain blank, and yesterday’s totals are hidden after local midnight.

Dotted links at Now connect the latest known SOC to the forecast. They hold the latest reading until Now and interpolate toward the first forecast point; they are display estimates, never stored as measurements or used to calculate forecast-error deltas.
