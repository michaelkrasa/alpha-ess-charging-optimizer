# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v3.0.0] - 2026-10-22

### Added
- EMHASS rolling MPC, replanned every 15 minutes from measured SOC, day-ahead prices, demand history and a calibrated weather-based solar forecast.
- Executable AlphaESS charge and household-supply windows, freshness and reserve checks, single-writer leases, and settings readback.
- Local dashboard with daily price statistics, expected/measured SOC and deltas, aligned solar forecasts, daily energy counters, deterministic plan summaries, and distinct schedule colors.
- A downloadable, self-contained dashboard demo using synthetic data.
- Docker/NAS deployment with persistent SQLite and runtime secrets, read-only previews, rollback, and a restricted one-time-authorized updater for future passwordless deployments.
- Setup, migration/adaptation guides, an open-source overlap assessment and roadmap, pinned dependencies, CI and Dependabot proposals.

### Changed
- The rolling manager's entry point is `main.py`; its configuration and commissioning workflow replace the earlier standalone CLI setup.
- Full-horizon EMHASS solves on low-power hosts use a 60-second budget and a 0.1% objective-gap limit, with solver certificates and independent physical replay.
- SOC curves use labelled display estimates to connect measurements through Now; estimates never replace measured data or forecast-error comparisons.
- Package and README versions now follow the existing GitHub release sequence rather than the stale `1.1.0` package value.

### Fixed
- Packaged the configuration module required by the older planner's optional comparison.
- Support DSM installations without `visudo`, verifying the narrow sudo rule through a harmless privileged probe and restoring it if verification fails.
- Preserve backfilled SOC when duplicate power-only samples arrive; hide previous-day energy totals after local midnight.

### Migration
- Start in observation mode using the new example configuration and follow [setup and commissioning](docs/SETUP.md) before live operation. Stop the old controller before enabling the new one.
- Weekly AlphaESS scheduling requires device permission; standard two-charge/two-household-supply windows can be rewritten throughout the day without it. Battery export is not implemented.
- Current tariff/time handling includes Czech/Prague assumptions. Lambda packaging remains experimental; NAS/Pi deployments require target-hardware validation.

## [v2.6.0] - 2026-01-27

- Reorganized application code, tests and utilities, updated imports and deployment packaging, and expanded historical-price validation.

Historical entries below describe the earlier standalone CLI; they are not validation of the current rolling manager. Current package metadata is in `pyproject.toml`.

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

[Unreleased]: https://github.com/michaelkrasa/alpha-ess-charging-optimizer/compare/v3.0.0...HEAD
[v3.0.0]: https://github.com/michaelkrasa/alpha-ess-charging-optimizer/compare/v2.6.0...v3.0.0
[v2.6.0]: https://github.com/michaelkrasa/alpha-ess-charging-optimizer/compare/v2.5.0...v2.6.0
[v2.5.0]: https://github.com/michaelkrasa/AlphaESS-charging-optimizer/compare/v2.4.0...v2.5.0
