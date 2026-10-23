"""Aggregate paired risk-policy replays and settle their ending inventory."""

import csv
import json
from datetime import date, timedelta
from pathlib import Path

from analysis.settle_emhass_inventory import inputs
from src.manager.daily import step
from src.manager.domain import PRAGUE, day_slots
from src.manager.settings import Settings
from src.manager.tariff import prices

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ("emhass", "partial", "hedged", "maintenance")


def season(name):
    prefix = ROOT / "analysis/output" / f"emhass_benchmark_hedge_{name}"
    report = json.loads(prefix.with_suffix(".json").read_text())
    with prefix.with_suffix(".csv").open() as source:
        rows = list(csv.DictReader(source))
    daily = {p: [r for r in rows if r["policy"] == p] for p in POLICIES}
    if (
        not all(daily.values())
        or len({tuple(r["day"] for r in values) for values in daily.values()}) != 1
    ):
        raise ValueError("Policies do not cover identical measured days")
    summary = {p: report["policies"][p] for p in POLICIES}
    settings = Settings.model_validate(report["settings"])
    capacity = settings.effective_capacity_kwh
    energy = {
        p: float(daily[p][-1]["ending_soc_percent"]) / 100 * capacity for p in POLICIES
    }
    initial_energy = dict(energy)
    tail_cash = dict.fromkeys(POLICIES, 0.0)
    tail_wear = dict(tail_cash)
    hours = 0.0
    converged = max(energy.values()) - min(energy.values()) < 1e-7
    reason = None
    if report["segments"] == 1:
        (days, _), measured, fx = inputs()
        last = date.fromisoformat(daily["emhass"][-1]["day"])
        for offset in range(1, 8):
            if converged:
                break
            day = last + timedelta(days=offset)
            if day not in measured or day not in days or days[day].spot is None:
                reason = "Contiguous measured/priced continuation unavailable"
                break
            for i, t in enumerate(day_slots(day)):
                buy, sell = prices(
                    float(days[day].spot[i]),
                    fx.get(day),
                    9 <= t.astimezone(PRAGUE).hour < 13,
                )
                for duration, load, pv, ev in measured[day][i]:
                    for p in POLICIES:
                        energy[p], imp, exp, dis, _, _ = step(
                            energy[p],
                            load,
                            pv,
                            False,
                            100,
                            capacity,
                            settings,
                            discharge=not ev,
                            duration_hours=duration,
                        )
                        tail_cash[p] += imp * buy - exp * sell
                        tail_wear[p] += dis * settings.wear_eur_per_kwh
                    hours += duration
                    converged = max(energy.values()) - min(energy.values()) < 1e-7
                    if converged:
                        break
                if converged:
                    break
    else:
        reason = "Multiple recording segments: retain prespecified inventory marks"
    result = {
        "days": report["days"],
        "summary": summary,
        "ending_kwh": initial_energy,
        "settlement": {
            "converged": converged,
            "hours": hours,
            "tail_cash_eur": tail_cash,
            "tail_wear_eur": tail_wear,
            "ending_kwh": energy,
            "unavailable_reason": reason,
        },
        "comparison": {},
    }
    base = summary["emhass"]
    for p in POLICIES:
        values = summary[p]
        daily_deltas = [
            float(b["cash_eur"]) - float(a["cash_eur"])
            for a, b in zip(daily["emhass"], daily[p])
        ]
        result["comparison"][p] = {
            "cash_benefit_eur": base["cash_eur"] - values["cash_eur"],
            "economic_benefit_eur": base["economic_eur"] - values["economic_eur"],
            "expensive_import_reduction_kwh": base.get("expensive_import_kwh", 0)
            - values.get("expensive_import_kwh", 0),
            "worst_daily_cash_increase_eur": max(daily_deltas),
            "settled_cash_benefit_eur": base["cash_eur"]
            + tail_cash["emhass"]
            - values["cash_eur"]
            - tail_cash[p]
            if converged
            else None,
            "settled_economic_benefit_eur": base["cash_eur"]
            + base["wear_eur"]
            + tail_cash["emhass"]
            + tail_wear["emhass"]
            - values["cash_eur"]
            - values["wear_eur"]
            - tail_cash[p]
            - tail_wear[p]
            if converged
            else None,
        }
    return result


def main():
    report = {name: season(name) for name in ("recent", "autumn")}
    target = ROOT / "analysis/output/hedging_comparison.json"
    target.write_text(json.dumps(report, indent=2))
    for name, r in report.items():
        print(
            name,
            r["days"],
            "days",
            "matched terminal inventory:",
            r["settlement"]["converged"],
        )
        for p, c in r["comparison"].items():
            print(p, json.dumps(c))


if __name__ == "__main__":
    main()
