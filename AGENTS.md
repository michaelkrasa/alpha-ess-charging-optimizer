# Working in this repository

This is an EMHASS-to-AlphaESS integration with a local dashboard. EMHASS owns optimization; the controller translates decisions into executable windows and confirms settings. Current tariffs/time handling include Czech/Prague assumptions.

- Setup or installation changes: read `docs/SETUP.md` and `NAS.md`.
- New inverter, market, forecast or AI-assisted adaptation: read `docs/ADAPTING.md` and `analysis/project_roadmap.md` before choosing scope.
- Planner/economic changes: read `analysis/emhass_validation.md`; compare identical inputs, terminal energy, losses and wear. Forecast/backtest savings are not realized savings.
- Tests: `uv run --frozen --extra emhass --extra dev pytest tests/ -q`. EMHASS adapter changes require actual solves, physical replay and target-host full-horizon performance checks.
- Use synthetic fixtures in Git. Credentials, raw household evidence, private configuration and operational outputs stay outside tracked files.
- Default to observation mode. One live writer per inverter; retain freshness checks, readiness, reserve, leases, write spacing and readback. A code change or passing test does not commission physical behavior.
- Verify dashboard changes with the read-only preview before deploying. Runtime secrets belong in host-private files/environment, never image layers or public screenshots.
- Dependency updates: preserve `uv.lock`, regenerate `requirements-server.txt`, and validate the EMHASS version-specific adapter before changing its pin.
