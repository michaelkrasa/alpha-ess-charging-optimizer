"""Read-only household price/plan diagnosis; never constructs a device client."""

from dataclasses import replace
from datetime import date, datetime
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.backtest_household import Forecaster, load_history
from src.manager.daily import plan_daily, score_candidates, step, in_window
from src.manager.domain import PRAGUE, Slot, Telemetry, day_slots
from src.manager.settings import Settings, load_settings
from src.manager.store import make_store
from src.manager.tariff import high_tariff, prices


def main():
    settings = load_settings()
    store = make_store(settings)
    day = date(2026, 10, 21)
    snapshot_path = Path("data/commissioning/price_day_snapshot_2026-10-21.json")
    if snapshot_path.exists():
        snapshot = json.loads(snapshot_path.read_text())
        settings = Settings.model_validate(snapshot["settings"])
        store = snapshot["state"]
    else:
        snapshot = {
            "settings": settings.model_dump(mode="json"),
            "state": {
                k: store.get(k)
                for k in [
                    f"prices:ote:{day}",
                    "eur_conversion",
                    "load_profile",
                    "plan",
                    "telemetry",
                    "device_schedule",
                ]
            },
        }
        snapshot_path.write_text(json.dumps(snapshot, indent=2, allow_nan=False))
        store = snapshot["state"]
    spot = store.get(f"prices:ote:{day}")
    fx = store.get("eur_conversion")["rate"]
    capacity = settings.effective_capacity_kwh or 17.2
    history, _ = load_history()
    forecaster = Forecaster(history, "weather")
    midnight = day_slots(day)[0]
    instants = day_slots(day)[1:]
    demand, pv = forecaster.forecast(midnight, instants)
    ceilings = store.get("load_profile")["ceiling_values"]
    slots = []
    for t, l, s in zip(instants, demand, pv):
        local = t.astimezone(PRAGUE)
        i = local.hour * 4 + local.minute // 15
        high = high_tariff(t, settings.high_tariff_periods)
        buy, sell = prices(spot[i], fx, high)
        slots.append(
            Slot(
                t.isoformat(),
                spot[i],
                buy,
                sell,
                float(l),
                float(s),
                "VT" if high else "NT",
                ceilings[str(i)],
            )
        )
    raw = json.loads(Path("data/commissioning/power_2026-10-21.json").read_text())
    raw = sorted(raw, key=lambda r: r["uploadTime"])
    initial = float(raw[0]["cbat"])
    state = Telemetry(midnight.isoformat(), initial, capacity, 0, 0, 0, 0)
    planned = plan_daily(slots, state, settings, minimum_slots=4)
    planned_c = tuple(
        (planned["charge"][f"timeChaf{j}"], planned["charge"][f"timeChae{j}"])
        for j in (1, 2)
        if planned["charge"]["gridCharge"]
        and planned["charge"][f"timeChaf{j}"] != planned["charge"][f"timeChae{j}"]
    )
    planned_d = (
        tuple(
            (planned["discharge"][f"timeDisf{j}"], planned["discharge"][f"timeDise{j}"])
            for j in (1, 2)
            if planned["discharge"][f"timeDisf{j}"]
            != planned["discharge"][f"timeDise{j}"]
        )
        if planned["discharge"]["ctrDis"]
        else None
    )

    # Search the user's two-valley pattern, rather than choosing one straw-man
    # window: every 15-minute start, with 15..120-minute charge windows, and
    # several peak-covering household supply windows. This is a diagnostic
    # candidate family, not a claim of a globally optimal schedule.
    def clock(i):
        return f"{i // 4:02}:{i % 4 * 15:02}"

    morning = [
        (clock(i), clock(i + n))
        for i in range(1, 24)
        for n in (1, 2, 4, 6, 8)
        if i + n <= 25
    ]
    afternoon = [
        (clock(i), clock(i + n))
        for i in range(40, 57)
        for n in (1, 2, 4, 6, 8)
        if i + n <= 58
    ]
    discharges = [
        (("06:15", "10:00"), ("15:30", "21:00")),
        (("06:00", "10:15"), ("15:00", "23:45")),
    ]
    candidates = [(planned_c, planned_d)] + [
        ((a, b), d)
        for a in morning
        for b in afternoon
        for d in discharges
        if not any(x < e and st < y for x, y in (a, b) for st, e in d)
    ]
    scores = score_candidates(slots, state, settings, candidates)
    best_index = 1 + int(np.argmin(scores[1:]))

    def describe(candidate):
        energy = capacity * initial / 100
        cash = wear = gridcharge = pvcharge = 0
        cp, dp = candidate
        for s in slots:
            t = datetime.fromisoformat(s.timestamp)
            energy, imp, exp, d, g, p = step(
                energy,
                s.load_kw,
                s.solar_kw,
                in_window(t, cp),
                100,
                capacity,
                settings,
                dp is None or in_window(t, dp),
            )
            cash += imp * s.import_eur_kwh - exp * s.export_eur_kwh
            wear += d * settings.wear_eur_per_kwh
            gridcharge += g
            pvcharge += p
        return {
            "charge_windows": cp,
            "house_supply_windows": dp,
            "cash_eur": cash,
            "wear_eur": wear,
            "ending_soc_percent": energy / capacity * 100,
            "grid_charge_kwh": gridcharge,
            "pv_charge_kwh": pvcharge,
        }

    # Current forecast comparison uses only the saved plan, without recomputing
    # weather at a later timestamp or pretending noon information existed at 00.
    live = store.get("plan")
    telemetry = store.get("telemetry")
    current_slots = [
        Slot(
            d["timestamp"],
            0,
            d["import_eur_kwh"],
            d["export_eur_kwh"],
            d["load_kw"],
            d["solar_kw"],
            "unknown",
            ceilings[
                str(
                    datetime.fromisoformat(d["timestamp"]).astimezone(PRAGUE).hour * 4
                    + datetime.fromisoformat(d["timestamp"]).astimezone(PRAGUE).minute
                    // 15
                )
            ],
        )
        for d in live["outlook_decisions"]
    ]
    current_state = Telemetry(
        telemetry["timestamp"], telemetry["soc_percent"], capacity, 0, 0, 0, 0
    )
    pair = store.get("device_schedule")
    c = pair["charge"]
    d = pair["discharge"]
    current_cp = tuple(
        (c[f"timeChaf{j}"], c[f"timeChae{j}"])
        for j in (1, 2)
        if c["gridCharge"] and c[f"timeChaf{j}"] != c[f"timeChae{j}"]
    )
    current_dp = (
        tuple(
            (d[f"timeDisf{j}"], d[f"timeDise{j}"])
            for j in (1, 2)
            if d[f"timeDisf{j}"] != d[f"timeDise{j}"]
        )
        if d["ctrDis"]
        else None
    )
    current_candidates = [(current_cp, current_dp)] + [
        ((a,), di)
        for a in afternoon
        if a[0]
        >= datetime.fromisoformat(current_slots[0].timestamp)
        .astimezone(PRAGUE)
        .strftime("%H:%M")
        for di in [current_dp, (("15:30", "21:00"),), (("15:00", "23:45"),)]
        if di is None or not any(a[0] < e and st < a[1] for st, e in di)
    ]
    current_scores = score_candidates(
        current_slots, current_state, settings, current_candidates
    )
    current_best = int(np.argmin(current_scores[1:])) + 1
    unconstrained = score_candidates(
        current_slots,
        current_state,
        settings,
        current_candidates,
        enforce_headroom=False,
    )
    unconstrained_best = int(np.argmin(unconstrained[1:])) + 1
    scenario_scores = np.array(
        [
            score_candidates(
                current_slots,
                replace(current_state, capacity_kwh=cap),
                settings,
                current_candidates,
            )
            for cap in settings.capacity_scenarios_kwh
        ]
    )
    robust = np.mean(scenario_scores, axis=0)
    robust_best = int(np.argmin(robust[1:])) + 1
    peak_start = datetime(2026, 10, 21, 6, 15, tzinfo=PRAGUE)
    peak_end = datetime(2026, 10, 21, 10, 15, tzinfo=PRAGUE)
    peak_import = peak_load = 0
    peak_soc = []
    for a, b in zip(raw, raw[1:]):
        ta = datetime.fromisoformat(a["uploadTime"]).replace(tzinfo=PRAGUE)
        tb = datetime.fromisoformat(b["uploadTime"]).replace(tzinfo=PRAGUE)
        duration = (
            max(0, (min(tb, peak_end) - max(ta, peak_start)).total_seconds()) / 3600
        )
        peak_import += float(a["gridCharge"]) / 1000 * duration
        peak_load += float(a["load"]) / 1000 * duration
        if peak_start <= ta < peak_end:
            peak_soc.append(float(a["cbat"]))
    result = {
        "day": str(day),
        "scope": "Forecast counterfactual, not realized full-day savings. Midnight forecast uses archived weather with six-hour release lag and completed history. First SOC sample is at "
        + raw[0]["uploadTime"]
        + ". Current comparison reuses the saved live forecast. No device writes.",
        "initial_soc_percent": initial,
        "midnight_planner": describe(candidates[0]),
        "best_two_valley_candidate": describe(candidates[best_index]),
        "two_valley_candidates": len(candidates) - 1,
        "two_valley_objective_minus_planner_eur": float(scores[best_index] - scores[0]),
        "current_soc_percent": telemetry["soc_percent"],
        "current_plan_created_at": live.get("created_at"),
        "current_best_afternoon_charge": current_candidates[current_best],
        "current_afternoon_objective_minus_installed_eur": float(
            current_scores[current_best] - current_scores[0]
        ),
        "current_robust_best_afternoon_charge": current_candidates[robust_best],
        "current_robust_objective_minus_installed_eur": float(
            robust[robust_best] - robust[0]
        ),
        "current_without_headroom_best_charge": current_candidates[unconstrained_best],
        "current_without_headroom_objective_minus_installed_eur": float(
            unconstrained[unconstrained_best] - unconstrained[0]
        ),
        "headroom_screen_at_1345": {
            "recent_peak_load_kw": ceilings["55"],
            "fixed_charge_kw": settings.charge_kw,
            "planning_import_limit_kw": settings.import_limit_kw,
        },
        "actual_morning_peak": {
            "local_range": "06:15-10:15",
            "measured_import_kwh": peak_import,
            "measured_load_kwh": peak_load,
            "minimum_soc_percent": min(peak_soc),
        },
    }
    Path("analysis/output/price_day_diagnosis_2026-10-21.json").write_text(
        json.dumps(result, indent=2, allow_nan=False)
    )
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
