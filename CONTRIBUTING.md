# Contributing

Start with a bug report, an observation-mode integration or a reproducible replay. For new inverter/market support, describe the real hardware and billing contract first; the [roadmap](analysis/project_roadmap.md) explains where integration adds value and where upstream work may be better.

```bash
uv sync --frozen --extra emhass --extra dev
uv run --frozen --extra emhass --extra dev pytest tests/ -q
```

Use synthetic fixtures and sanitized logs. Keep credentials, serial numbers, location and raw consumption exports private. Explain what changed, the tests/replays used, device limitations and whether behavior was physically verified. Passing unit tests alone is not hardware certification.

For AI-assisted work, start with [the adaptation guide](docs/ADAPTING.md). Small adapters with clear capabilities and evidence are more useful than a speculative universal framework.

Dependency updates arrive through Dependabot and need review. Regenerate the container dependency export after lockfile changes. EMHASS upgrades require model and full-horizon performance validation as well as ordinary tests.
