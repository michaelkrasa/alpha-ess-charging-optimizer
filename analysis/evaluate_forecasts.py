"""Read-only, causal next-quarter forecast diagnostics on household history."""

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.backtest_household import Forecaster, load_history
from src.manager.domain import PRAGUE, day_slots


def evaluate(days, start, end):
    forecaster = Forecaster(days, "weather")
    rows = []
    failures = {}
    for d in sorted(k for k in days if start <= k <= end):
        for now in day_slots(d):
            target = now + timedelta(minutes=15)
            td = target.astimezone(PRAGUE).date()
            if td not in days:
                continue
            i = (
                target.astimezone(PRAGUE).hour * 4
                + target.astimezone(PRAGUE).minute // 15
            )
            try:
                load, pv = forecaster.forecast(now, [target])
            except (ValueError, KeyError) as exc:
                failures[str(exc)] = failures.get(str(exc), 0) + 1
                continue
            rows.append(
                (
                    float(load[0]),
                    float(pv[0]),
                    float(days[td].load[i]),
                    float(days[td].solar[i]),
                )
            )
    a = np.array(rows)
    if not len(a):
        raise ValueError("No forecast observations")
    peak = a[:, 2] >= np.percentile(a[:, 2], 90)
    return {
        "start": str(start),
        "end": str(end),
        "samples": len(a),
        "information": "Completed prior days for demand; archived weather with six-hour release lag for PV. Future actuals only used for scoring.",
        "scope": "Next-quarter forecast accuracy, not a savings estimate. Causal PV calibration includes the bounded seven-day cloudy fallback.",
        "load_mae_kw": float(np.mean(np.abs(a[:, 0] - a[:, 2]))),
        "load_bias_kw": float(np.mean(a[:, 0] - a[:, 2])),
        "solar_mae_kw": float(np.mean(np.abs(a[:, 1] - a[:, 3]))),
        "solar_bias_kw": float(np.mean(a[:, 1] - a[:, 3])),
        "net_load_mae_kw": float(
            np.mean(np.abs((a[:, 0] - a[:, 1]) - (a[:, 2] - a[:, 3])))
        ),
        "top_decile_actual_load_threshold_kw": float(np.percentile(a[:, 2], 90)),
        "top_decile_load_bias_kw": float(np.mean(a[peak, 0] - a[peak, 2])),
        "failures": failures,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--output", default="analysis/output/forecast_diagnostics.json")
    args = p.parse_args()
    days, _ = load_history()
    result = evaluate(days, args.start, args.end)
    Path(args.output).write_text(json.dumps(result, indent=2, allow_nan=False))
    print(json.dumps(result, indent=2))
