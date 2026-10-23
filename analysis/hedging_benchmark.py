"""Read-only paired rolling ablations on private historical measurements.

Run with python -m analysis.hedging_benchmark. No device client or API calls.
"""

import argparse
import csv
from datetime import date, datetime, timedelta
from pathlib import Path

from analysis.emhass_benchmark import evaluate, read_forecasts
from src.manager.domain import PRAGUE
from src.manager.settings import Settings


def historical_errors(period):
    """Original cached daily forecasts scored only on completed preceding days."""
    root = Path(__file__).resolve().parents[1]
    load = read_forecasts(period)
    pv = read_forecasts(period, "pv")
    errors = []
    with (root / "data/emhass/household_15min.csv").open() as source:
        for row in csv.DictReader(source):
            t = datetime.fromisoformat(row["timestamp"]).astimezone(PRAGUE)
            f = load.get(t.date(), {}).get(t)
            g = pv.get(t.date(), {}).get(t)
            if not f or not g:
                continue
            if any(
                t - datetime.fromisoformat(r["asof"]) < timedelta(hours=2)
                for r in (f, g)
            ):
                continue
            error = (
                float(row["household_load_w"])
                - float(row["pv_w"])
                - float(f["seven_day_mean"])
                + float(g["baseline_pv_w"])
            ) / 1000
            errors.append({"timestamp": t.isoformat(), "error_kw": error})
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--period", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--policies", default="emhass,partial,hedged,maintenance")
    args = parser.parse_args()
    base = Settings(
        control_mode="legacy_daily",
        standard_planner="emhass",
        effective_capacity_kwh=14.2,
        charge_kw=8.6,
        discharge_kw=8.6,
        high_tariff_periods=[["09:00", "13:00"]],
        exclude_inferred_grid_only_ev=True,
    )
    partial = base.model_copy(update={"charge_step_minutes": 5})
    hedged = partial.model_copy(update={"hedge_enabled": True})
    maintenance = hedged.model_copy(update={"full_charge_interval_days": 14})
    policies = {"partial": partial, "hedged": hedged, "maintenance": maintenance}
    selected = args.policies.split(",")
    if not set(selected) <= {"emhass", *policies}:
        parser.error("Unknown replay policy")
    evaluate(
        args.start,
        args.end,
        args.period,
        base,
        selected,
        tag=args.tag,
        pv_method="baseline_cached",
        policy_settings={p: s for p, s in policies.items() if p in selected},
        risk_errors=historical_errors(args.period),
    )


if __name__ == "__main__":
    main()
