"""Freeze household measurements with inferred EV separation for package evaluation."""

from datetime import timedelta
import csv, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.audit_ev_separation import load_samples
from analysis.backtest_household import integrate_day
from src.manager.consumption import separate_grid_only_load
from src.manager.domain import PRAGUE, day_slots


def prepare():
    raw = load_samples()
    canonical = [
        dict(
            uploadTime=r["timestamp"].isoformat(),
            load=r["load"] * 1000,
            ppv=r["pv"] * 1000,
            gridCharge=max(r["grid"], 0) * 1000,
            feedIn=max(-r["grid"], 0) * 1000,
            cbat=r["soc"],
            pchargingPile=r["ev_meter"] * 1000,
        )
        for r in raw
    ]
    cleaned = separate_grid_only_load(canonical)
    out = Path("data/emhass")
    out.mkdir(parents=True, exist_ok=True)
    five = []
    byday = {}
    for r in cleaned:
        from datetime import datetime

        t = datetime.fromisoformat(r["uploadTime"])
        row = {
            "timestamp": r["uploadTime"],
            "site_load_w": float(r["site_load"]),
            "household_load_w": float(r["load"]),
            "pv_w": float(r["ppv"]),
            "grid_w": float(r["gridCharge"]) - float(r["feedIn"]),
            "battery_ac_w": float(r["site_load"])
            - float(r["ppv"])
            - float(r["gridCharge"])
            + float(r["feedIn"]),
            "soc_percent": float(r["cbat"]),
            "ev_inferred": int(r["ev_grid_only"]),
        }
        five.append(row)
        byday.setdefault(t.astimezone(PRAGUE).date(), []).append(row)
    with (out / "household_5min.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=list(five[0]))
        w.writeheader()
        w.writerows(five)
    quarter = []
    excluded = {}
    for day, rows in sorted(byday.items()):
        normalized = [
            dict(
                timestamp=r["timestamp"],
                load_kw=r["household_load_w"] / 1000,
                pv_kw=r["pv_w"] / 1000,
                grid_import_kw=max(r["grid_w"], 0) / 1000,
                grid_export_kw=max(-r["grid_w"], 0) / 1000,
            )
            for r in rows
        ]
        try:
            integrated = integrate_day(day, normalized)
        except ValueError as exc:
            excluded[str(day)] = str(exc)
            continue
        for i, t in enumerate(day_slots(day)):
            stop = t + timedelta(minutes=15)
            observed = [
                r
                for r in rows
                if t
                <= __import__("datetime").datetime.fromisoformat(r["timestamp"])
                < stop
            ]
            quarter.append(
                dict(
                    timestamp=t.isoformat(),
                    household_load_w=integrated.load[i] * 1000,
                    pv_w=integrated.solar[i] * 1000,
                    ev_inferred=int(any(r["ev_inferred"] for r in observed)),
                )
            )
    with (out / "household_15min.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=list(quarter[0]))
        w.writeheader()
        w.writerows(quarter)
    report = {
        "source_samples": len(five),
        "complete_quarter_hours": len(quarter),
        "complete_days": len(quarter) // 96,
        "ev_inferred_samples": sum(r["ev_inferred"] for r in five),
        "ev_inferred_quarters": sum(r["ev_inferred"] for r in quarter),
        "excluded_days": excluded,
        "note": "EV labels are inferred from measured load steps, battery balance and SOC; no functioning dedicated EV meter. Household targets during inferred sessions are baseline estimates and must not be scored as measured labels. Site load and grid bills remain intact.",
    }
    Path("analysis/output/emhass_dataset_manifest.json").write_text(
        json.dumps(report, indent=2)
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    prepare()
