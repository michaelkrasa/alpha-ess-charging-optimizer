# AlphaESS Charging Optimizer - Agent Context

This document provides context for AI agents working with this codebase.

## Project Overview

The AlphaESS Charging Optimizer is a Python application that automatically optimizes battery charging and discharging schedules based on Czech day-ahead electricity prices (OTE). It uses dynamic price analysis to detect valleys (cheap periods) and peaks (expensive periods) and creates arbitrage cycles to maximize savings.

## Project Structure

```
├── src/                    # Core application modules
│   ├── optimizer.py        # Main optimizer orchestration (ESSOptimizer class)
│   ├── models.py           # Data models (PriceWindow, ArbitrageCycle, OptimizationPlan, etc.)
│   ├── price_analyzer.py   # Price analysis and valley/peak detection (PriceAnalyzer class)
│   ├── battery_manager.py  # Battery state calculations (BatteryManager class)
│   ├── ess_client.py       # AlphaESS API client (ESSClient class)
│   └── price_cache.py      # Price caching logic (PriceCache class)
├── utils/                  # Utility scripts
│   └── fetch_december_prices.py  # Price data fetcher for testing
├── tests/                  # Test suite
│   ├── test_ess.py         # Main test suite
│   ├── test_december_2025.py  # December 2025 price data tests
│   └── test_data/          # Test price data files (JSON format)
├── config.py               # Configuration loader (Config class)
├── config.yaml             # Optimization settings (YAML)
├── lambda_handler.py       # AWS Lambda entry point
├── Dockerfile              # Lambda container (arm64 for Graviton)
├── deploy-lambda.sh        # One-command AWS deployment script
├── .env.example            # Environment template
└── pyproject.toml          # Project dependencies (UV format)
```

## Dependency Management

**This project uses UV for dependency management**, not pip.

- Install dependencies: `uv sync`
- Run Python scripts: `uv run python -m src.optimizer`
- Run tests: `uv run pytest tests/ -v`
- Add dependency: Edit `pyproject.toml`, then run `uv sync`

## Running the Application

### Local Execution

```bash
# Default: Optimize for today
uv run python -m src.optimizer

# Dry run for a specific day (no API changes)
uv run python -m src.optimizer --date 15

# Dry run mode (no API changes)
uv run python -m src.optimizer --dry-run
```

### Testing

```bash
# Run all tests
uv run pytest tests/ -v

# Run specific test file
uv run pytest tests/test_ess.py -v

# Run December 2025 data tests
uv run pytest tests/test_december_2025.py -v
```

## AWS Lambda Deployment

The project includes a deployment script for AWS Lambda:

```bash
# Configure .env with AWS credentials and AlphaESS API credentials
./deploy-lambda.sh
```

The script:
1. Builds a Docker image (arm64 for Lambda Graviton)
2. Pushes to Amazon ECR
3. Updates Lambda function code and configuration
4. Sets environment variables (APP_ID, APP_SECRET, SERIAL_NUMBER)

**Lambda Handler**: `lambda_handler.py` → `lambda_handler.lambda_handler`

**Dockerfile**: Uses `public.ecr.aws/lambda/python:3.12` (arm64 platform)

## Key Concepts

### Price Analysis
- Uses 15-minute price slots (96 slots per day)
- Detects valleys: `price < mean / price_multiplier`
- Detects peaks: `price > mean × price_multiplier`
- Applies smoothing to reduce noise
- Finds mid-day dips between peaks for additional opportunities

### Arbitrage Cycles
- Pairs charge windows (valleys) with discharge windows (peaks)
- Maximum 2 cycles per day (AlphaESS API limitation)
- Sizes windows based on actual battery SOC and capacity
- Accounts for household consumption between windows

### Battery Management
- Reads actual SOC from AlphaESS API
- Calculates charging slots needed based on SOC gap
- Estimates SOC drain from consumption
- Respects min_soc and max_soc limits

## Import Patterns

### Within src/ (relative imports)
```python
from .models import PriceWindow, SLOTS_PER_DAY
from .price_analyzer import PriceAnalyzer
```

### From outside src/ (absolute imports)
```python
from src.optimizer import ESSOptimizer
from src.models import PriceWindow
```

### Configuration
```python
from config import Config  # config.py is in root, not in src/
```

## Configuration

### Environment Variables (.env)
- `APP_ID`: AlphaESS API app ID
- `APP_SECRET`: AlphaESS API secret
- `SERIAL_NUMBER`: ESS serial number
- `AWS_ACCOUNT_ID`: AWS account for ECR (Lambda deployment)
- `ECR_REPO`: ECR repository name (Lambda deployment)

### Configuration File (config.yaml)
- `charge_to_full`: Hours to charge 0→100%
- `price_multiplier`: Threshold factor vs daily mean
- `min_soc`: Minimum discharge SOC %
- `max_soc`: Target charge SOC %
- `avg_day_load_kw`: Average household load for SOC estimation
- `min_window_slots`: Minimum window size (×15 min)
- `smoothing_window`: Price smoothing window (×15 min)

## API Integration

### AlphaESS API
- Uses `alphaessopenapi` package
- Client: `ESSClient` in `src/ess_client.py`
- Methods: `get_battery_soc()`, `get_battery_capacity()`, `set_charging_schedule()`, `set_discharge_schedule()`

### Price Data
- Uses `ote_cr_price_fetcher` package
- Fetches 15-minute granularity prices from Czech OTE day-ahead market
- Caches prices for today and tomorrow in `PriceCache`

## Testing

### Test Structure
- `tests/test_ess.py`: Main test suite with mocked dependencies
- `tests/test_december_2025.py`: Tests against real December 2025 price data
- `tests/test_data/december_2025/`: JSON files with price data (one per day)

### Test Data
Price data files are JSON arrays of 96 floats (one per 15-minute slot).

### Running Tests
Tests use pytest with asyncio support. Mock patches use `src.` prefix:
```python
with patch('src.optimizer.Config') as mock_config, \
     patch('src.ess_client.alphaess') as mock_client:
```

## Common Tasks

### Adding a New Module
1. Add file to `src/`
2. Use relative imports within `src/`
3. Update `__init__.py` if exporting public API
4. Update imports in `src/optimizer.py` if needed

### Modifying Configuration
- Edit `config.yaml` for optimization settings
- Edit `.env` for credentials (never commit `.env`)
- `Config` class in `config.py` loads both

### Updating Dependencies
1. Edit `pyproject.toml`
2. Run `uv sync`
3. Test thoroughly before committing

### Lambda Deployment Changes
- Update `Dockerfile` if adding files
- Ensure `lambda_handler.py` imports use `src.` prefix
- Test locally before deploying

## Notes for AI Agents

1. **Always use UV**, not pip
2. **Import paths**: Use `src.` prefix when importing from outside `src/`
3. **Config location**: `config.py` is in root, not in `src/`
4. **Test data path**: Use `Path(__file__).parent / 'test_data'` in tests
5. **Lambda context**: Lambda runs in `/tmp` for cache, uses environment variables for config
6. **Timezone**: Uses `ZoneInfo('Europe/Prague')` by default (Czech timezone)
