# Evaluation reports and reproduction

Start with [EMHASS validation and adoption](emhass_validation.md), then the [benchmark protocol](emhass_benchmark_protocol.md), [forecast evaluation](emhass_forecast_evaluation.md) and [package integration research](emhass_integration_research.md).

These reports record the model, evaluated periods, assumptions and known limitations at the time of each experiment. Earlier reports describe superseded policies; their claims should not be combined as if they tested the same controller. Deployment status in dated research reports is historical; the project README and NAS guide describe current operation.

Household API archives, measured consumption/PV, invoice material, exact live settings, generated plots and operational snapshots stay private under ignored `data/`, `analysis/output/`, `output/` and `tmp/` directories. References to those artifacts in reports identify local evidence; those files are not included in the public repository. Reproduction requires your own input records with the same schema and causal forecast timing. Analysis scripts expose their arguments through `--help` where applicable.

Only aggregate comparison figures and the analysis source are versioned. A backtest is evidence for choosing a policy under its stated model, not proof of future savings.
