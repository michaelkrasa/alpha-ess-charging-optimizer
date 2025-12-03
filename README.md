# AlphaESS Charging Optimizer (Dynamic Reactive Strategy)

Optimize AlphaESS battery charging/discharging against Czech day‑ahead electricity prices (15‑minute slots). This project dynamically detects price valleys and peaks to create
arbitrage cycles - charging when cheap and discharging when expensive.

- Language/Tooling: Python 3.12 + uv
- Data sources: OTE day‑ahead prices (15‑min) + AlphaESS API
- Run modes: Continuous monitoring or single‑shot (for cron)

## Key Features

- Dynamic pattern detection
    - Smooths prices and derives thresholds from the day’s mean price
    - Valleys (charge): price < mean / price_multiplier
    - Peaks (discharge): price > mean × price_multiplier
    - Also finds mid‑day dips between peaks for extra charging opportunities
- Arbitrage cycles
    - Pairs each valley with the next sequential peak (no skipping peaks)
    - Extends discharge windows to cover all profitable hours (price > charge_price × 1.2)
    - Falls back to price-based discharge detection when no peaks found
    - Up to 2 cycles per day (AlphaESS API limitation)
- Battery‑aware sizing
    - Window length sized to real charging need based on current SOC and `charge_to_full`
    - Full valley charging for overnight periods or depleted batteries (SOC < 30%)
    - Accounts for household consumption between charge/discharge windows
    - Pulls battery capacity from the device (gross × usable %) when available
- Reactive operations
    - Continuous mode re‑checks hourly and adapts to the rest of the day
    - Evening planning runs at 18:00 when next‑day prices are published
    - Critical SOC handling triggers re‑optimization

## Quick Start

Prerequisites:

- Python 3.12+
- uv (fast Python package/dependency manager)

1) Clone the repository

```bash
git clone https://github.com/michaelkrasa/AlphaESS-charging-optimizer.git
cd AlphaESS-charging-optimizer
```

2) Create a `.env` file with your AlphaESS credentials

```dotenv
APP_ID=your_alpha_ess_app_id
APP_SECRET=your_alpha_ess_app_secret
SERIAL_NUMBER=your_system_serial
```

Notes:

- Do not commit this file. Keep credentials out of version control.

3) Review/adjust `config.yaml` for non‑secret settings

```yaml
# AlphaESS API credentials (optional here; .env overrides these)
app_id: your_alpha_ess_app_id
app_secret: your_alpha_ess_app_secret
serial_number: your_system_serial

# Optimization behavior
charge_to_full: 3          # hours to charge 0→100%
price_multiplier: 1.2      # valley/peak threshold factor vs. daily mean

# Battery parameters
charge_rate_kw: 6.0
avg_peak_load_kw: 2.5
avg_overnight_load_kw: 1.6   # standby consumption for SOC drain estimation
min_soc: 10
max_soc: 100

# Technical parameters (15‑min slots)
min_window_slots: 2
smoothing_window: 2
```

4) Sync dependencies (from `pyproject.toml`)

```bash
uv sync
```

5) Run

- Continuous monitoring (recommended):
  ```bash
  uv run ESS.py
  ```
- Single‑shot (e.g., cron at 18:00):
  ```bash
  uv run ESS.py --once
  ```

## How It Works (ESS.py)

- Fetches 96 price slots (15‑min) for a target day
- Smooths prices (moving average) and computes daily mean
- Derives thresholds using `price_multiplier`:
    - Valley: `price < mean / price_multiplier`
    - Peak: `price > mean * price_multiplier`
- Detects contiguous regions above/below thresholds, merges overlaps, and adds mid‑peak valleys
- Builds arbitrage cycles by pairing each valley with the next peak
- Sizes charging windows to actual need (~SOC to 100%) while respecting valley length
- Programs AlphaESS API:
    - Charging: `updateChargeConfigInfo` (up to 2 windows)
    - Discharging: `updateDisChargeConfigInfo` (up to 2 windows)

Battery awareness:

- SOC from `LastPower.soc`
- Capacity from `cobat` × `usCapacity`% when available
- Estimates charging time and discharge SOC impact using configured rates

## Run Modes

- Continuous (`uv run ESS.py`)
    - 18:00: plan next day (when next‑day prices are available)
    - Hourly: reactive re‑analysis for the remainder of the day
    - Critical SOC (< 20%): triggers immediate re‑optimization
- Single run (`uv run ESS.py --once`)
    - Plans for tomorrow and exits (useful for cron/schedulers)

## Logging

Logs to both console and file:

- `logs/ess_optimizer.log`

## Configuration and Secrets

- Non‑secret configuration lives in `config.yaml`.
- Secrets and device identifiers live in `.env`.
- `config.py` loads `.env` automatically and overrides these keys from `config.yaml` if present:
    - `app_id` ← `APP_ID`
    - `app_secret` ← `APP_SECRET`
    - `serial_number` ← `SERIAL_NUMBER`

## Testing

Use pytest via uv:

```bash
# Run tests
uv run pytest -v
```

## Automation Examples

Cron (Linux/macOS) — run once at 18:00 daily:

```cron
0 18 * * * cd /path/to/AlphaESS-charging-optimizer && uv run ESS.py --once
```

## Notes

- Target market: Czech day‑ahead prices (OTE), 15‑minute granularity.
- AlphaESS API limitations restrict to two charge/discharge windows per day.

Happy arbitrage! ⚡️🔋
