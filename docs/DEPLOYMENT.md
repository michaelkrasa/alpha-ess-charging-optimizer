# Deployment choices

| Target | Application | State | Evidence / remaining work |
| --- | --- | --- | --- |
| Local computer / Linux | `main.py serve` | SQLite on local disk | Supported development/runtime path |
| Docker / NAS | `Dockerfile.nas` + Compose | Bind-mounted SQLite | ARM64 Synology exercised; performance depends on host/horizon |
| Raspberry Pi | 64-bit OS + same server container | Local persistent disk | ARM64 packaging is reusable; actual Pi commissioning still needed |
| AWS Lambda | `lambda_handler.py`, SAM scaffold | DynamoDB | Current full EMHASS configuration not validated end to end |

## Full-horizon performance

The initial 15-second / 0.01% gap setting timed out on a low-power ARM64 NAS after next-day publication extended the horizon. The adapter now allows 60 seconds and a 0.1% relative objective gap, retaining the complete horizon and independent physical replay. Six 131-quarter checks passed on that NAS (slowest 41.6 seconds), followed by a fresh live 128-quarter plan in 19.9 seconds and confirmed inverter settings. [Validation evidence](../analysis/nas_solver_validation.md).

Performance depends on hardware, inputs and load. Require a fresh successful controller cycle after updates; an image smoke test or HTTP heartbeat alone does not establish planner health.

For an always-on household, start with [Docker Compose](../NAS.md). It keeps scheduling, dashboard and durable state together, without buying cloud capacity.

## AWS Lambda status

The repository retains Lambda packaging and SAM infrastructure. The small ZIP path excludes EMHASS, so it cannot run the default EMHASS configuration unchanged. A viable deployment must prove scientific dependency packaging, cold-start/solve duration, scheduled execution, durable leases, authentication and cost under the target account's current AWS pricing. DynamoDB is the intended persistent store; SQLite in Lambda `/tmp` is not durable shared state.

Do not promise an always-free cloud deployment. Budget alerts and measured resource use belong in cloud commissioning. Never expose the passwordless local dashboard through a public Function URL; cloud authentication is a separate requirement.

## Updates and versions

The project version is in `pyproject.toml`; `uv.lock` records exact dependency resolution. `requirements-server.txt` is exported from that lock for containers:

```bash
uv export --frozen --extra emhass --no-dev --no-emit-project --no-hashes -o requirements-server.txt
```

Dependabot proposes weekly `uv`, Docker and GitHub Actions updates. Review dependency license/API changes, regenerate the export and run tests. EMHASS is pinned because the adapter extends its constraint-building interfaces. Planner updates also require representative physical replay and full-horizon performance checks before deployment. Dependency PRs do not update a running controller automatically.

Generic signed multi-architecture release images, tagged releases and automated hardware benchmarks are roadmap items rather than existing distribution guarantees.
