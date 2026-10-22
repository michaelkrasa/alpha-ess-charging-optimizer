"""Summarize completed offline replays without selecting/tuning a policy."""

import csv
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis/output"


def daily_costs(report, tag):
    capacity = report["settings"]["effective_capacity_kwh"]
    efficiency = report["settings"]["discharge_efficiency"]
    previous = {}
    result = {}
    with (OUT / f"emhass_benchmark_{tag}.csv").open() as f:
        for row in csv.DictReader(f):
            day, policy = date.fromisoformat(row["day"]), row["policy"]
            last_day, last_soc = previous.get(policy, (None, 50))
            if last_day != day - timedelta(days=1):
                last_soc = 50
            soc = float(row["ending_soc_percent"])
            credit = (soc - last_soc) / 100 * capacity * efficiency * 0.10
            cash, wear = float(row["cash_eur"]), float(row["wear_eur"])
            result.setdefault(day, {})[policy] = {
                "cash": cash,
                "economic": cash + wear - credit,
            }
            previous[policy] = day, soc
    # Daily energy-value accounting must telescope to reported segment totals.
    for policy, totals in report["policies"].items():
        total = sum(rows[policy]["economic"] for rows in result.values())
        if abs(total - totals["economic_eur"]) > 1e-7:
            raise ValueError("Daily/segment terminal accounting differs")
    return result


def uncertainty(daily, metric):
    deltas = np.array(
        [r["current"][metric] - r["emhass"][metric] for _, r in sorted(daily.items())]
    )
    if len(deltas) < 28:
        return {
            "emhass_saving_eur": float(deltas.sum()),
            "descriptive_7day_block_95pct_eur": None,
            "note": "Fewer than four seven-day blocks; no uncertainty interval reported. A single-week circular bootstrap is degenerate.",
        }
    rng = np.random.default_rng(4721)
    samples = []
    for _ in range(2000):
        starts = rng.integers(0, len(deltas), size=(len(deltas) + 6) // 7)
        indices = np.concatenate([(s + np.arange(7)) % len(deltas) for s in starts])[
            : len(deltas)
        ]
        samples.append(float(deltas[indices].sum()))
    return {
        "emhass_saving_eur": float(deltas.sum()),
        "descriptive_7day_block_95pct_eur": np.quantile(
            samples, [0.025, 0.975]
        ).tolist(),
        "note": "Circular blocks over available evaluation days; inspected research periods, not an untouched holdout or measured live savings.",
    }


def summarize():
    reports = {}
    for path in sorted(OUT.glob("emhass_benchmark_*.json")):
        if path.stem.endswith("_progress") or path.stem.endswith("smoke"):
            continue
        tag = path.stem.removeprefix("emhass_benchmark_")
        p = json.loads(path.read_text())
        if not isinstance(p, dict):
            continue
        if "current" not in p.get("policies", {}) or "emhass" not in p["policies"]:
            continue
        daily = daily_costs(p, tag)
        a, b = p["policies"]["current"], p["policies"]["emhass"]
        credit_sensitivity = {}
        for value in (0, 0.10, 0.20, 0.30):
            credit_sensitivity[str(value)] = (
                a["cash_eur"]
                + a["wear_eur"]
                - a["terminal_credit_eur"] * value / 0.10
                - (
                    b["cash_eur"]
                    + b["wear_eur"]
                    - b["terminal_credit_eur"] * value / 0.10
                )
            )
        reports[tag] = {
            "days": p["days"],
            "segments": p["segments"],
            "cash": uncertainty(daily, "cash"),
            "economic": uncertainty(daily, "economic"),
            "emhass_saving_by_terminal_eur_per_kwh": credit_sensitivity,
            "current_cash_eur": a["cash_eur"],
            "emhass_cash_eur": b["cash_eur"],
            "current_economic_eur": a["economic_eur"],
            "emhass_economic_eur": b["economic_eur"],
            "reserve_violations": {
                k: v.get("reserve_violations", 0) for k, v in p["policies"].items()
            },
            "battery_during_ev_kwh": {
                k: v.get("battery_discharge_during_ev_kwh", 0)
                for k, v in p["policies"].items()
            },
            "emhass_failures": b["failures"],
            "emhass_projection_intervals": b.get("projected_first_actions", 0),
        }
    (OUT / "emhass_comparison_summary.json").write_text(json.dumps(reports, indent=2))
    for tag, r in reports.items():
        print(
            f"{tag}: {r['days']}days; EMHASS saving cash {r['cash']['emhass_saving_eur']:+.3f}, economic {r['economic']['emhass_saving_eur']:+.3f} EUR"
        )
    return reports


if __name__ == "__main__":
    summarize()
