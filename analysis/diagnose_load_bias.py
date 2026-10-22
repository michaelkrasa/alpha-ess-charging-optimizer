"""Read-only causal ablation: change only the seven-day load estimator.

No production forecast settings or inverter commands are changed. Results are
diagnostics on already observed periods, not an independent new test set.
"""

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import analysis.backtest_household as backtest


Base = backtest.Forecaster


class MedianLoad(Base):
    def __init__(self, days, kind, **kwargs):
        super().__init__(days, kind, load_method="median")


Original = MedianLoad


class MeanLoad(Original):
    def profiles(self, day):
        _, solar = super().profiles(day)
        past = [self.days.get(day - timedelta(days=i)) for i in range(1, 8)]
        past = [d for d in past if d is not None]
        return np.mean([d.load for d in past], axis=0), solar


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 9, 8))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 19))
    parser.add_argument("--output", default="analysis/output/load_bias_diagnostic")
    args = parser.parse_args()
    days, _ = backtest.load_history()
    settings = backtest.load_settings()
    exchange = backtest.Exchange(backtest.load_fx())
    results = {}
    for name, forecaster in [("median", Original), ("mean", MeanLoad)]:
        backtest.Forecaster = forecaster
        summary, records = backtest.evaluate(
            days,
            exchange,
            settings,
            args.start,
            args.end,
            "weather",
            "schedule",
            ["native", "legacy", "standard_daily"],
        )
        evaluated = sorted({date.fromisoformat(row["day"]) for row in records})
        f = forecaster(days, "persistence")
        actual = predicted = 0.0
        for d in evaluated:
            load, _ = f.profiles(d)
            actual += sum(days[d].load) * 0.25
            predicted += sum(load) * 0.25
        summary["load_profile_diagnostic"] = {
            "actual_kwh": actual,
            "forecast_kwh": float(predicted),
            "energy_bias_percent": float((predicted / actual - 1) * 100),
            "note": "Completed-day seven-day profile versus measured energy; not exact intraday forecast-error aggregation.",
        }
        results[name] = summary
        print(
            name,
            json.dumps(
                {
                    "bias_percent": summary["load_profile_diagnostic"][
                        "energy_bias_percent"
                    ],
                    "costs": {
                        k: v["economic_cost_eur"] for k, v in summary["totals"].items()
                    },
                }
            ),
            flush=True,
        )
    backtest.Forecaster = Base
    Path(args.output).with_suffix(".json").write_text(
        json.dumps(results, indent=2, allow_nan=False)
    )


if __name__ == "__main__":
    main()
