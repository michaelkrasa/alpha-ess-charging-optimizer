"""Audit likely grid-only EV sessions; retain uncertainty without inventing a meter."""

from datetime import datetime, timedelta
import csv, json
from pathlib import Path
import sys
import statistics

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.manager.domain import PRAGUE


def load_samples():
    samples = {}
    with Path("analysis/output/alphaess_power_2025.csv").open() as f:
        for r in csv.DictReader(f):
            t = datetime.fromisoformat(r["timestamp"]).replace(tzinfo=PRAGUE)
            samples[t] = {
                "timestamp": t,
                "load": float(r["load_kw"]),
                "pv": float(r["pv_kw"]),
                "grid": float(r["grid_import_kw"]) - float(r["grid_export_kw"]),
                "soc": float(r["battery_soc_pct"]),
                "ev_meter": float(r["charging_pile_kw"]),
            }
    for p in Path("data/backtests/household").glob("*.json"):
        try:
            payload = json.loads(p.read_text())
            rows = payload["samples"]
        except (TypeError, KeyError):
            continue
        for r in rows:
            t = datetime.fromisoformat(r["uploadTime"]).replace(tzinfo=PRAGUE)
            samples[t] = {
                "timestamp": t,
                "load": float(r["load"]) / 1000,
                "pv": float(r["ppv"]) / 1000,
                "grid": (float(r["gridCharge"]) - float(r["feedIn"])) / 1000,
                "soc": float(r["cbat"]),
                "ev_meter": float(r.get("pchargingPile") or 0) / 1000,
            }
    return sorted(samples.values(), key=lambda r: r["timestamp"])


def detect_sessions(rows):
    events = []
    active = []
    baseline = None

    def finish():
        nonlocal active, baseline
        if len(active) >= 3:
            duration = (
                active[-1]["timestamp"] - active[0]["timestamp"]
            ).total_seconds() / 60 + 5
            balanced = sum(
                abs(r["load"] - r["pv"] - r["grid"]) <= 0.6 for r in active
            ) / len(active)
            soc_swing = max(r["soc"] for r in active) - min(r["soc"] for r in active)
            if 15 <= duration <= 600 and balanced >= 0.8 and soc_swing <= 2:
                events.append(
                    {
                        "start": active[0]["timestamp"].isoformat(),
                        "end": (
                            active[-1]["timestamp"] + timedelta(minutes=5)
                        ).isoformat(),
                        "duration_minutes": duration,
                        "baseline_kw": baseline,
                        "median_total_load_kw": statistics.median(
                            r["load"] for r in active
                        ),
                        "estimated_ev_increment_kw": statistics.median(
                            r["load"] for r in active
                        )
                        - baseline,
                        "battery_idle_fraction": balanced,
                        "soc_swing_percent": soc_swing,
                        "sample_timestamps": [
                            r["timestamp"].isoformat() for r in active
                        ],
                    }
                )
        active = []
        baseline = None

    for i, r in enumerate(rows):
        past = [
            x
            for x in rows[max(0, i - 6) : i]
            if 0 < (r["timestamp"] - x["timestamp"]).total_seconds() <= 1800
        ]
        base = min((x["load"] for x in past), default=r["load"])
        idle = abs(r["load"] - r["pv"] - r["grid"]) <= 0.6
        if active:
            if (
                (r["timestamp"] - active[-1]["timestamp"]).total_seconds() > 900
                or r["load"] - baseline < 4.5
                or not idle
            ):
                finish()
            else:
                active.append(r)
                continue
        increment = r["load"] - base
        if 5.5 <= increment <= 12 and r["load"] >= 6 and idle and past:
            active = [r]
            baseline = base
    finish()
    return events


def main():
    rows = load_samples()
    events = detect_sessions(rows)
    byyear = {}
    for e in events:
        y = e["start"][:4]
        v = byyear.setdefault(y, {"sessions": 0, "hours": 0, "estimated_ev_kwh": 0})
        v["sessions"] += 1
        v["hours"] += e["duration_minutes"] / 60
        v["estimated_ev_kwh"] += (
            e["estimated_ev_increment_kw"] * e["duration_minutes"] / 60
        )
    marked = {t for e in events for t in e["sample_timestamps"]}
    high = sum(r["load"] >= 6 for r in rows)
    report = {
        "method": "Likely EV: sustained 5.5-12kW step above recent baseline, >=15min, >=80% near-zero battery balance and <=2ppSOC swing. Inferred labels, not a measured EV meter. Outcomes only; completed past days may be used for training.",
        "samples": len(rows),
        "direct_ev_meter_positive": sum(r["ev_meter"] > 0.1 for r in rows),
        "high_load_samples_ge6kw": high,
        "inferred_ev_samples": len(marked),
        "events_by_year": byyear,
        "example_sessions": [
            {k: v for k, v in e.items() if k != "sample_timestamps"}
            for e in events[-12:]
        ],
    }
    Path("data/commissioning/ev_inferred_sessions.json").write_text(
        json.dumps(events, indent=2)
    )
    Path("analysis/output/ev_separation_audit.json").write_text(
        json.dumps(report, indent=2)
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
