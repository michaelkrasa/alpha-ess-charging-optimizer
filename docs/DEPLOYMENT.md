# Deployment choices

| Target | Application | State | Evidence / remaining work |
| --- | --- | --- | --- |
| Local computer / Linux | `main.py serve` | SQLite on local disk | Supported development/runtime path |
| Docker / NAS | `Dockerfile.nas` + Compose | Bind-mounted SQLite | ARM64 Synology exercised; performance depends on host/horizon |
| Raspberry Pi | 64-bit OS + same server container | Local persistent disk | ARM64 packaging is reusable; actual Pi commissioning still needed |
| AWS Lambda | `lambda_handler.py`, SAM scaffold | DynamoDB | Current full EMHASS configuration not validated end to end |

## Current performance caveat

A low-power ARM64 NAS hit the 15-second EMHASS solve limit after next-day publication expanded the horizon beyond 24 hours (observed 22 October 2026). The same captured inputs solved on a faster development host; CPU-constrained container replay reproduced `User_Limit`. The final target-host remedy is still under investigation. An image smoke test or HTTP heartbeat does not establish planner health: require a fresh successful full-horizon controller cycle before accepting a deployment.

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
