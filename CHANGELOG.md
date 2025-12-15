# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.5.0] - 2025-12-13
### Added
- Discharge validation in arbitrage cycles: Ensures discharge windows are sufficient for charged energy, using SOC gains and average load calculations with 5% buffer.
- Overnight charging extensions: Automatically extends short overnight valleys (up to 20% above average price) to meet full charging duration requirements.
- `calculate_full_discharge_slots()` in BatteryManager: Estimates slots needed to discharge from 95% to 20% SOC, capacity-aware with fallback approximation and 20% buffer.
- Period formatting helper in ESSClient for concise logging.

### Changed
- Logging in schedule setters: Consolidated to single-line summaries (e.g., "✓ Charging enabled: 00:00→03:00").
- CLI: Made `--dry-run` explicit; `--date` now supports live runs on specific dates.
- Config: `price_multiplier` adjusted from 1.2 to 1.18 for more aggressive low-price charging.

### Files Changed
- `battery_manager.py`: +11 lines (new discharge slots method).
- `config.yaml`: `price_multiplier` 1.2 → 1.18.
- `ess_client.py`: +15 lines (period formatting and log cleanup).
- `optimizer.py`: +109 lines (validation, extension logic, CLI updates).

This release enhances arbitrage reliability, especially for overnight cycles, while maintaining compatibility.

[Unreleased]: https://github.com/michaelkrasa/AlphaESS-charging-optimizer/compare/v2.5.0...HEAD
[v2.5.0]: https://github.com/michaelkrasa/AlphaESS-charging-optimizer/compare/v2.4.0...v2.5.0
