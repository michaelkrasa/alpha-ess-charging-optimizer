# Adapt this project with a coding assistant

Start with an observation-only configuration and one concrete installation. Tell your assistant the inverter model/API, battery topology, tariff, market area and deployment target. Supply secrets through private runtime configuration, and keep raw household evidence outside Git.

## A useful first prompt

> Read AGENTS.md, docs/SETUP.md and analysis/project_roadmap.md. Adapt an observation-only configuration for my installation. Identify assumptions that differ from the current AlphaESS/Czech integration. Reuse EMHASS; implement only the needed integration. Verify forecasts, energy accounting and executable schedules with synthetic tests and my private replay data. Keep live writes disabled until I commission the hardware behavior.

## Find the change

| Need | Start here | Evidence before live use |
| --- | --- | --- |
| Battery physics / capability | `settings.py`, `daily.py`, `emhass_mpc.py` | SOC, power and energy replay match executable device actions |
| Another inverter | `device.py`, `legacy_device.py`, `controller.py`, `emhass_schedule.py` | Read/write/readback, partial failure and native restoration tests |
| Another price area / contract | `providers.py`, `tariff.py`, `domain.py` | Timestamp alignment, DST and representative invoice calculations |
| Demand / PV forecast | `load_profile.py`, `consumption.py`, `providers.py` | Walk-forward results without future-data leakage |
| Dashboard | `dashboard.html`, `web.py` | Local read-only preview; stale/reconnecting/error states |
| Another host | `Dockerfile.nas`, Compose, `store.py` | Full priced horizon on target hardware; durable state and single writer |

Paths above are relative to `src/manager/`, except deployment files at the root. These are existing integration points, not a stable third-party plugin API.

## Work in small, reviewable steps

1. Describe supported commands and physical behavior: grid charge, household supply, hold, export, power setpoints and schedule limits differ by device.
2. Add synthetic fixtures and a failing test for the specific behavior. Use private historical inputs for replay, not committed credentials or raw household exports.
3. Preserve EMHASS as the optimizer and translate only actions the hardware can execute. Compare economic cost, terminal battery energy and modeled wear under the same inputs.
4. Run `uv run --frozen --extra emhass --extra dev pytest tests/ -q`. For dependency changes, regenerate the container requirements export and test the built image.
5. Verify in observation mode on the intended host. Commission explicit control/readback behavior before one live writer is enabled.

AI assists implementation and explanation; production actuation follows tested code and bounded configuration. Add a second physical integration before extracting a generic plugin framework. See the roadmap for acceptance gates and upstream alternatives.
