# AlphaESS household battery manager

A local energy manager for an AlphaESS residential battery, with an EMHASS rolling optimizer and a tablet-friendly dashboard. It replans every 15 minutes using the measured battery state, published electricity prices, expected household demand and weather-based solar forecasts.

The household installation runs in Docker on a Synology NAS. It uses the AlphaESS **cloud API** to read telemetry and update inverter settings; Home Assistant is not required.

## Open the dashboard

**Household address:** [http://nasnas.local:8765](http://nasnas.local:8765)
**IP fallback:** [http://10.0.0.11:8765](http://10.0.0.11:8765)
**Port:** `8765/TCP`

Open it from a phone, computer or tablet on the home network. The local dashboard intentionally has no username or password. Keep this port private; use a trusted VPN for remote access rather than forwarding it to the internet.

The dashboard shows:

- Automatic control ON/OFF, controller health and latest successful AlphaESS contact.
- Measured battery SOC and power, with the inverter's measurement timestamp.
- Proposed actions alongside charge and household-supply windows read back from the inverter.
- Today/tomorrow price charts with a linear scale, midnight boundaries and daily minimum, maximum and mean prices.
- Today's measured site demand and solar generation, filling from midnight as readings arrive.

**ON** lets the manager update schedules after its readiness checks pass. **OFF** requests restoration of native solar charging and household self-consumption; the dashboard shows restoration as pending until confirmed. Monitoring continues while control is off. A green HTTP heartbeat means the web server responds; cloud contact and inverter measurement freshness are separate indicators.

## How decisions work

1. Read current SOC, timestamped power measurements and installed inverter schedules.
2. Build 15-minute demand and solar forecasts aligned to the market-price intervals.
3. Run EMHASS model predictive control over the available published-price horizon, up to 24 hours.
4. Translate the next action into the inverter's available charge and household-discharge windows.
5. Send changed settings, then read both configurations back. Replan at the next quarter hour.

Full-night charging is allowed when expected savings justify conversion losses, assumed battery wear and any displaced solar capture. Unknown tomorrow prices are never invented; the horizon extends once day-ahead prices are available, normally around 15:00 Europe/Prague.

This household uses `legacy_daily`: the **standard time-of-day API format**, not a once-per-day update limit. It stores two charge and two household-discharge windows at a time. Repeated rewrites allow the manager to change them throughout the day. The weekly scheduling API returns `6017` on this system and is not required.

The active planner is `standard_planner: emhass`. With `emhass_shadow: true`, the previous search runs as a read-only comparison using the same initial SOC, prices and forecasts. It cannot write a second schedule.

### Physical and financial assumptions

- Battery reserve: **10%**. Maximum charging target: **100%**.
- Battery export is disabled; discharge means supplying the house. Only surplus PV is sold.
- The EV is grid-only and blocks battery discharge during charging. Likely EV sessions are excluded from battery-demand training; their measured consumption remains in site billing. This inference is uncertain because there is no connected EV meter channel.
- Grid charging is modeled at 8.6 kW. Effective SOC-to-energy capacity is 14.2 kWh; API nominal battery capacity is 17.2 kWh.
- Charge/discharge efficiencies are 95% each way. Battery wear is an explicit assumption of EUR 0.045 per discharged kWh.
- There is **no default grid-import power cap**. An optional explicit cap can be configured for other installations.
- All optimizer, dashboard and accounting amounts use **EUR**. Fixed standing charges are excluded from marginal charging decisions.

The tariff adapter includes supplier commodity/service charges, VT/NT distribution, system services, electricity tax and VAT. Non-EUR contract fee inputs are converted at ingestion using the CNB EUR rate. There is no extra flat EUR 115/MWh transmission adder.

OTE is the default market-price source. ENTSO-E is supported with `price_source: entsoe` and an `ENTSOE_TOKEN`. The checked 21 October 2026 OTE and ENTSO-E series matched for all 96 quarter hours.

Demand uses a seven-day measured profile. Solar uses location/orientation, Open-Meteo tilted irradiance and calibration against recent measured PV. Actual EMHASS learned load and solar forecasting tools were evaluated, but did not consistently improve both tested seasons; they are not enabled just because they are available.

## Run on a computer

Requires Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
# Fill APP_ID, APP_SECRET and SERIAL_NUMBER in .env.
chmod 600 .env
uv sync --extra emhass --extra dev
uv run --extra emhass python main.py serve --host 127.0.0.1 --port 8765
```

This starts monitoring/planning without inverter writes. Open [http://127.0.0.1:8765](http://127.0.0.1:8765). Configure `manager.yaml` and `solar_config.yaml` for the installation before enabling live operation.

For a commissioned installation:

```bash
uv run --extra emhass python main.py serve --live
```

`--live` allows device writes; persisted dashboard control must also be ON. A fresh database starts OFF. The default server bind is `0.0.0.0`, allowing LAN access. **Run only one live manager against an inverter.** The household Mac instance was stopped during the NAS handover; do not start a second live instance there.

Other commands:

```bash
uv run --extra emhass python main.py once
uv run --extra emhass python main.py mode on --live
uv run --extra emhass python main.py mode off --live
uv run --extra emhass python main.py replay --date 2025-12-10 --power-file /path/to/private/power.csv
```

`once` without `--live` never changes the inverter. `main.py` is the application entry point. `src.optimizer` is the historical standalone optimizer, retained for comparison.

## Run with Docker / Synology

Use **`Dockerfile.nas`**, not the Lambda Dockerfile. The container includes EMHASS and runs as UID/GID `10001`, with a read-only root filesystem, writable `/tmp`, bounded logs and persistent SQLite state.

```bash
# In a dedicated directory containing the project files:
mkdir -p data
sudo chown 10001:10001 data
chmod 600 .env
# Set NAS_BIND_IP to the NAS LAN IP in .env.
docker compose --env-file .env -f compose.nas.yaml up -d --build
```

The base Compose file starts without live writes. After commissioning and stopping any previous controller:

```bash
docker compose --env-file .env -f compose.nas.yaml -f compose.nas.live.yaml up -d
```

Enable automatic control in the dashboard after checking cloud contact and readiness. `restart: unless-stopped` handles ordinary process restarts and NAS boots. The healthcheck tests HTTP availability; inspect dashboard health for actual controller/cloud status.

See [NAS.md](NAS.md) for the household deployment, unsupported RS819 installation, recovery, updates, backups and maintenance. The one-time `ops/nas/deploy.py` script is specifically pinned to this household's reviewed hardware/DSM/package and private deployment payload; it is not a general Synology installer.

### Persistence and backups

Runtime state is in `data/manager.sqlite3`: ON/OFF intent, write spacing, measurements, forecasts, prices and history. Keep it on the NAS filesystem, not an SMB-mounted database. Back up through SQLite's backup operation or stop the container first; copying only a live database file can miss WAL contents.

Credentials stay in `.env` and are passed at runtime. They are not baked into the image. Raw household datasets, deployment bundles, operational snapshots and local screenshots are excluded from Git.

## Readiness and limitations

New writes require fresh measurements, complete priced/forecast intervals, verified tariff switching times and explicit commissioning. A schedule readback confirms the inverter stored the settings; measured power shows what the battery was actually doing at its timestamp. Neither proves future execution or realized savings.

The current household HDO schedule is verified only for **20–26 October 2026**: VT 09:00–13:00, NT otherwise. **Recheck and update `tariff_valid_from` / `tariff_valid_until` before 27 October.** The controller blocks new schedules when that verification expires.

Standard schedules repeat daily. A cloud outage or expired forecast does not automatically cancel an already-installed schedule. Charge and discharge updates are separate, non-atomic calls; partial changes remain unconfirmed and safe staging/readback is used. Device writes are serialized with at least ten seconds between requests. Read-only cloud monitoring runs about once a minute; the dashboard heartbeat runs every five seconds.

The standard API's maximum endpoint is 23:45, so it cannot represent the final quarter as an ordinary same-day window. Horizons crossing a daylight-saving clock change are rejected rather than mapped ambiguously. Direct LAN/Modbus inverter control has not been configured.

## Validation

The current suite has **580 passing tests and 131 skips**. It covers actual EMHASS solves, executable schedules, energy accounting, EV separation, forecast causality, price/time alignment, reserve constraints, stale data, control leases, partial writes, dashboard feedback and bounded NAS recovery. Skips belong to existing historical price-window cases; they are not successful checks.

```bash
uv run --extra emhass --extra dev pytest tests/ -q
```

[EMHASS validation](analysis/emhass_validation.md) records comparable walk-forward replays. Over 42 recent days and 90 autumn days, EMHASS had lower modeled cash/economic costs than the previous live search and original once-daily policy. These are inspected historical scenarios, **not guaranteed annual savings or measured invoice reconciliation**. Assumed battery physics, inferred EV sessions, archived weather and historical tariff scenarios limit the conclusions.

[Analysis guide](analysis/README.md) explains the reports and private reproduction inputs. Earlier daily-only figures are retained as historical diagnostics and do not validate the rolling policy.

## Project layout

| Path | Purpose |
| --- | --- |
| `main.py`, `src/manager/cli.py` | CLI and server entry point |
| `src/manager/controller.py` | Single-writer control, readiness and schedule confirmation |
| `src/manager/emhass_mpc.py` | EMHASS rolling planner and appliance constraints |
| `src/manager/providers.py` | Telemetry, prices and forecast inputs |
| `src/manager/web.py`, `dashboard.html` | API and local dashboard |
| `manager.yaml`, `solar_config.yaml` | Installation settings and solar geometry |
| `compose.nas*.yaml`, `Dockerfile.nas` | Persistent local/container deployment |
| `ops/nas/` | Pinned commissioning and bounded boot recovery |
| `analysis/`, `tests/` | Evaluation tools, reports and automated checks |

## AWS status

Lambda packaging and SAM infrastructure are retained, but **this EMHASS household configuration is deployed to the NAS, not AWS**. The small Lambda ZIP excludes EMHASS and cannot run this configuration unchanged. Do not treat the scaffold as a validated alternative deployment. Cloud dashboard authentication remains required; local passwordless behavior must not be copied to a public Function URL.
