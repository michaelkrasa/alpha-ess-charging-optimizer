<p align="center">
  <h1 align="center">⚡ AlphaESS Charging Optimizer</h1>
  <p align="center">
    <strong>Dynamic battery arbitrage against Czech day-ahead electricity prices</strong>
  </p>
  <p align="center">
    <a href="#-quick-start">Quick Start</a> •
    <a href="#-aws-lambda-deployment">Lambda Deploy</a> •
    <a href="#-how-it-works">How It Works</a> •
    <a href="#%EF%B8%8F-configuration">Configuration</a>
  </p>
</p>

---

Automatically charge your AlphaESS battery when electricity is cheap and discharge when expensive. Uses 15-minute price slots from OTE (Czech day-ahead market) to maximize savings through smart arbitrage cycles.

```
┌─────────────────────────────────────────────────────────────────┐
│  💰 CHEAP (Valley)          💸 EXPENSIVE (Peak)                 │
│  ════════════════          ═══════════════════                  │
│  03:00-06:00 @ 45€         17:00-20:00 @ 180€                   │
│  ↓ CHARGE ↓                ↓ DISCHARGE ↓                        │
│  Grid → Battery            Battery → Home                       │
│                                                                 │
│  Spread: 135 €/MWh  ✨                                          │
└─────────────────────────────────────────────────────────────────┘
```

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🔍 **Dynamic Detection** | Auto-detects valleys & peaks from daily price patterns |
| 🔄 **Arbitrage Cycles** | Pairs charge windows with discharge windows for max spread |
| 🔋 **Battery-Aware** | Sizes windows based on actual SOC and capacity |
| 📊 **15-min Granularity** | Uses OTE's 96 daily price slots for precision |
| ☁️ **Serverless Ready** | Deploy to AWS Lambda or run locally |

## 🚀 Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (fast Python package manager)
- AlphaESS Open API credentials

### Installation

```bash
# Clone
git clone https://github.com/michaelkrasa/AlphaESS-charging-optimizer.git
cd AlphaESS-charging-optimizer

# Install dependencies
uv sync
```

### Configuration

Create a `.env` file with your credentials:

```bash
cp .env.example .env
# Edit .env with your values
```

```dotenv
# Required - AlphaESS API
APP_ID=your_alpha_ess_app_id
APP_SECRET=your_alpha_ess_app_secret
SERIAL_NUMBER=your_system_serial
```

Tune behavior in `config.yaml`:

```yaml
charge_to_full: 3        # Hours to charge 0→100%
price_multiplier: 1.2    # Valley/peak threshold vs daily mean
min_soc: 10              # Don't discharge below this %
max_soc: 100             # Charge target %
```

### Run

```bash
# Default: Single optimization for today (run at midnight)
uv run optimizer.py

# Dry run for a specific day (no API changes)
uv run optimizer.py --date 15
```

---

## ☁️ AWS Lambda Deployment

Run as a serverless function - no server required, pay only for execution time.

### Quick Deploy

```bash
# 1. Configure (edit .env with AWS settings)
cp .env.example .env

# 2. Deploy to AWS
./deploy-lambda.sh
```

The script will:
- ✅ Build Docker image (arm64 for Graviton)
- ✅ Push to Amazon ECR
- ✅ Update Lambda function
- ✅ Configure environment variables

### Lambda Configuration

| Setting | Value |
|---------|-------|
| **Architecture** | arm64 (Graviton) |
| **Timeout** | 30 seconds |
| **Memory** | 256 MB |
| **Trigger** | EventBridge @ 00:00 UTC daily |

### Lambda Execution

Lambda runs once at 00:00 daily and optimizes for the current day. No configuration needed - just schedule it via EventBridge.

### Schedule with EventBridge

```bash
# Daily at 00:00 UTC (01:01 CET)
aws events put-rule \
  --name "ess-daily-optimization" \
  --schedule-expression "cron(1 0 * * ? *)"
```

---

## 🧠 How It Works

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Fetch 96    │────▶│  Detect      │────▶│  Build       │
│  Price Slots │     │  Valleys &   │     │  Arbitrage   │
│  (15-min)    │     │  Peaks       │     │  Cycles      │
└──────────────┘     └──────────────┘     └──────────────┘
                                                 │
                                                 ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Done! ✅    │◀────│  Program     │◀────│  Size to     │
│              │     │  AlphaESS    │     │  Battery     │
│              │     │  API         │     │  SOC         │
└──────────────┘     └──────────────┘     └──────────────┘
```

### Price Analysis

1. **Smooth** prices with moving average to reduce noise
2. **Calculate** daily mean price
3. **Detect valleys:** `price < mean / price_multiplier`
4. **Detect peaks:** `price > mean × price_multiplier`
5. **Find** mid-day dips between peaks for extra opportunities

### Arbitrage Matching

- Each valley pairs with the next sequential peak
- Discharge windows extend to cover all profitable hours
- Up to **2 cycles per day** (AlphaESS API limitation)

### Battery Intelligence

- Reads actual SOC from device
- Pulls capacity (gross × usable %)
- Sizes charge windows to actual need
- Accounts for consumption between windows

---

## ⚙️ Configuration

### Environment Variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `APP_ID` | ✅ | AlphaESS API app ID |
| `APP_SECRET` | ✅ | AlphaESS API secret |
| `SERIAL_NUMBER` | ✅ | Your ESS serial number |
| `AWS_ACCOUNT_ID` | Lambda | AWS account for ECR |
| `ECR_REPO` | Lambda | ECR repository name |

### Optimization Settings (`config.yaml`)

| Setting | Default | Description |
|---------|---------|-------------|
| `charge_to_full` | 3 | Hours to charge 0→100% |
| `price_multiplier` | 1.2 | Threshold factor vs daily mean |
| `min_soc` | 10 | Minimum discharge SOC % |
| `max_soc` | 100 | Target charge SOC % |
| `avg_day_load_kw` | 1.8 | Avg household load for SOC estimation |
| `min_window_slots` | 2 | Minimum window size (×15 min) |
| `smoothing_window` | 2 | Price smoothing window (×15 min) |

---

## 📁 Project Structure

```
├── optimizer.py        # Main optimizer orchestration
├── models.py           # Data models (PriceWindow, ArbitrageCycle, etc.)
├── price_analyzer.py   # Price analysis and valley/peak detection
├── battery_manager.py  # Battery state calculations
├── ess_client.py       # AlphaESS API client
└── price_cache.py      # Price caching logic
├── config.py           # Configuration loader
├── config.yaml         # Optimization settings
├── lambda_handler.py   # AWS Lambda entry point
├── Dockerfile          # Lambda container (arm64)
├── deploy-lambda.sh    # One-command AWS deployment
├── .env.example        # Environment template
└── test_ess.py         # Test suite
```

---

## 🧪 Testing

```bash
uv run pytest test_ess.py -v
```

---

## ⏰ Automation

### Cron (Linux/macOS)

```cron
# Run daily at 00:00 (midnight + 1 minute)
1 0 * * * cd /path/to/AlphaESS-charging-optimizer && uv run optimizer.py
```

### AWS Lambda + EventBridge

See [Lambda Deployment](#️-aws-lambda-deployment) section above.

---

## 📝 Notes

- **Target market:** Czech OTE day-ahead prices (15-min granularity)
- **API limitation:** Max 2 charge + 2 discharge windows per day
- **Schedule:** Run at 00:00 daily to optimize for that day (prices are published the day before)

---

## 📄 License

MIT

---

<p align="center">
  <strong>Happy arbitrage! ⚡🔋</strong>
</p>
