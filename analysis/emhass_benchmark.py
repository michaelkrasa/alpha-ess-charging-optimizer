"""Executable, queued rolling comparison. Offline: no device client is imported.

Measured site power is integrated at its original five-minute resolution.
Inferred EV sessions block battery discharge; their grid bill is retained.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analysis.backtest_household import (
    Exchange,
    Forecaster,
    load_fx,
    load_history,
    make_slots,
)
from src.manager.daily import in_window, plan_daily, step
from src.manager.domain import PRAGUE, Telemetry, day_slots
from src.manager.emhass_mpc import EmhassPlanner
from src.manager.emhass_schedule import schedule_from_decisions
from src.manager.settings import load_settings
from src.manager.tariff import prices


def measurement_segments(path=ROOT / "data/emhass/household_5min.csv"):
    """Same boundary extension/gap rules as integrate_day, retain EV timing."""
    grouped = {}
    with path.open() as source:
        for row in csv.DictReader(source):
            t = datetime.fromisoformat(row["timestamp"]).astimezone(PRAGUE)
            grouped.setdefault(t.date(), []).append((t, row))
    out = {}
    for day, values in grouped.items():
        if len(day_slots(day)) != 96:
            continue
        values.sort(key=lambda x: x[0])
        midnight = day_slots(day)[0]
        points = [(int((t - midnight).total_seconds()), r) for t, r in values]
        if len(points) < 200 or points[0][0] > 600 or 86400 - points[-1][0] > 600:
            continue
        if max(b[0] - a[0] for a, b in pairwise(points)) > 900:
            continue
        points[0] = (0, points[0][1])
        quarters = [[] for _ in range(96)]
        for (a, r), (b, _) in zip(points, points[1:] + [(86400, points[-1][1])]):
            while a < b:
                i = a // 900
                end = min(b, (i + 1) * 900)
                quarters[i].append(
                    (
                        (end - a) / 3600,
                        float(r["site_load_w"]) / 1000,
                        float(r["pv_w"]) / 1000,
                        bool(int(r["ev_inferred"])),
                    )
                )
                a = end
        out[day] = quarters
    return out


def read_forecasts(period, kind=""):
    cache = {}
    for path in (ROOT / "data/emhass/forecasts" / kind / period).glob("*.csv"):
        with path.open() as source:
            cache[date.fromisoformat(path.stem)] = {
                datetime.fromisoformat(r["timestamp"]): r
                for r in csv.DictReader(source)
            }
    return cache


def with_load_forecast(slots, now, cache, method, intraday=None):
    rows = cache.get(now.astimezone(PRAGUE).date(), {})
    result, fallbacks = [], 0
    path = None
    if method == "intraday" and intraday is not None:
        path = intraday.get(int(now.timestamp() * 1e9))
    for i, s in enumerate(slots):
        t = datetime.fromisoformat(s.timestamp)
        row = rows.get(t)
        if row is None or datetime.fromisoformat(row["asof"]) > now:
            raise ValueError("Causal forecast cache missing")
        column = (
            "RidgeRegression" if method in {"ridge", "intraday"} else "seven_day_mean"
        )
        value = float(row[column])
        if path is not None:
            lead = int((t - now).total_seconds() / 900)
            if 0 <= lead < len(path):
                value = float(path[lead])
        if not np.isfinite(value):
            value = float(row["seven_day_mean"])
            fallbacks += 1
        if not np.isfinite(value):
            raise ValueError("No finite causal load forecast")
        result.append(replace(s, load_kw=max(0, value / 1000)))
    return result, fallbacks


def controls(pair, now):
    if not pair:
        return False, True
    c, d = pair["charge"], pair["discharge"]
    charge = bool(c["gridCharge"]) and in_window(
        now, [(c[f"timeChaf{j}"], c[f"timeChae{j}"]) for j in (1, 2)]
    )
    supply = not d["ctrDis"] or in_window(
        now, [(d[f"timeDisf{j}"], d[f"timeDise{j}"]) for j in (1, 2)]
    )
    return charge, supply


def evaluate(
    start,
    end,
    period,
    settings,
    policies,
    load_method="mean",
    ev_mode="inferred",
    tag="pilot",
    pv_method="weather",
    trace=False,
):
    logging.disable(logging.CRITICAL)
    days, _excluded = load_history()
    measured = measurement_segments()
    forecasts = read_forecasts(period)
    pv_forecasts = read_forecasts(period, "pv") if pv_method != "weather" else None
    # PV calibration uses measured PV only; demand is overwritten by causal cache.
    forecaster = Forecaster(days, "weather")
    exchange = Exchange(load_fx())
    planner = EmhassPlanner(settings)
    capacity = settings.effective_capacity_kwh or 17.2
    initial = capacity * 0.5
    energy = {p: initial for p in policies}
    installed, pending, last_success, last_daily = {}, {}, {}, {}
    totals = {p: Counter() for p in policies}
    failures = {p: Counter() for p in policies}
    solve_times = {p: [] for p in policies}
    records, skips = [], {}
    interval_records = []
    previous = None
    segments = 0
    intraday = None
    if load_method == "intraday":
        z = np.load(ROOT / "data/emhass/forecasts/intraday_2026-10-12_2026-10-18.npz")
        intraday = dict(zip(z["asof_utc_ns"], z["load_w"]))
    for day in sorted(d for d in days if start <= d <= end):
        actual = days[day]
        if actual.spot is None or day not in measured or day not in forecasts:
            skips[str(day)] = "Measured/pricing/causal forecast day unavailable"
            continue
        try:
            forecaster.profiles(day)
            for run in {forecaster.available_run(t) for t in day_slots(day)}:
                forecaster.weather(run)
            forecaster.scale(day)
        except ValueError as exc:
            skips[str(day)] = str(exc)
            continue
        if previous is None or day != previous + timedelta(days=1):
            if previous is not None:
                for p in policies:
                    totals[p]["terminal_credit_eur"] += (
                        (energy[p] - initial) * settings.discharge_efficiency * 0.10
                    )
            energy = {p: initial for p in policies}
            installed, pending, last_success, last_daily = {}, {}, {}, {}
            segments += 1
        previous = day
        daily = {p: Counter() for p in policies}
        for i, now in enumerate(day_slots(day)):
            for p in policies:
                if p in pending:
                    installed[p] = pending.pop(p)
            buy, sell = prices(
                float(actual.spot[i]),
                exchange.get(day),
                9 <= now.astimezone(PRAGUE).hour < 13,
            )
            try:
                slots = make_slots(now, days, forecaster, exchange, "schedule")
                slots, fallbacks = with_load_forecast(
                    slots, now, forecasts, load_method, intraday
                )
                if pv_forecasts is not None:
                    column = (
                        "emhass_adjusted_pv_w"
                        if pv_method == "emhass"
                        else "baseline_pv_w"
                    )
                    rows = pv_forecasts[day]
                    slots = [
                        replace(
                            s,
                            solar_kw=max(
                                0,
                                float(rows[datetime.fromisoformat(s.timestamp)][column])
                                / 1000,
                            ),
                        )
                        for s in slots
                    ]
                forecast_error = None
            except ValueError as exc:
                slots = None
                forecast_error = str(exc)
                fallbacks = 0
            for p in policies:
                if p in {"native", "no_battery"}:
                    continue
                if p in {"once_current", "old_once"}:
                    if now.astimezone(PRAGUE).hour < 15 or last_daily.get(p) == day:
                        continue
                    last_daily[p] = day
                state = Telemetry(
                    now.isoformat(), energy[p] / capacity * 100, capacity, 0, 0, 0, 0
                )
                began = time.perf_counter()
                try:
                    if slots is None:
                        raise ValueError(forecast_error)
                    if p == "emhass":
                        decisions = planner.plan(slots, state)
                        proposal = schedule_from_decisions(
                            decisions, slots, state, settings, installed.get(p)
                        )
                        daily[p]["projected_first_actions"] += int(
                            proposal["first_action_projection"]
                        )
                    else:
                        proposal = plan_daily(
                            slots,
                            state,
                            settings,
                            minimum_slots=4,
                            current_schedule=installed.get(p),
                            baseline_only=p == "old_once",
                        )
                    pending[p] = proposal
                    last_success[p] = now
                    daily[p]["replans"] += 1
                    daily[p]["forecast_fallback_slots"] += fallbacks
                except ValueError as exc:
                    failures[p][str(exc)] += 1
                    if (
                        p not in last_success
                        or (now - last_success[p]).total_seconds() > 1800
                    ):
                        installed.pop(p, None)
                        pending.pop(p, None)
                solve_times[p].append(time.perf_counter() - began)
            for p in policies:
                charge, supply = controls(installed.get(p), now)
                interval_initial = energy[p]
                cash_before = daily[p]["cash_eur"]
                load_energy = solar_energy = ev_hours = held_energy = 0.0
                for duration, load, pv, ev in measured[day][i]:
                    blocked = ev if ev_mode == "inferred" else False
                    if ev_mode == "broad":
                        blocked = ev or load >= 6
                    load_energy += load * duration
                    solar_energy += pv * duration
                    ev_hours += duration if blocked else 0
                    if (
                        not charge
                        and not supply
                        and not blocked
                        and energy[p] > capacity * settings.reserve_percent / 100 + 1e-7
                    ):
                        held_energy += max(0, load - pv) * duration
                    if p == "no_battery":
                        imported, exported = (
                            max(0, load - pv) * duration,
                            max(0, pv - load) * duration,
                        )
                        discharged = grid = pvcharge = 0
                    else:
                        previous_energy = energy[p]
                        energy[p], imported, exported, discharged, grid, pvcharge = (
                            step(
                                energy[p],
                                load,
                                pv,
                                charge,
                                settings.maximum_percent,
                                capacity,
                                settings,
                                discharge=supply and not blocked,
                                duration_hours=duration,
                            )
                        )
                        if (
                            abs(
                                energy[p]
                                - (
                                    previous_energy
                                    + (grid + pvcharge) * settings.charge_efficiency
                                    - discharged / settings.discharge_efficiency
                                )
                            )
                            > 1e-7
                        ):
                            raise AssertionError("Battery energy balance failed")
                    if (
                        abs(
                            imported
                            + pv * duration
                            + discharged
                            - (load * duration + exported + grid + pvcharge)
                        )
                        > 1e-7
                    ):
                        raise AssertionError("AC site energy balance failed")
                    d = daily[p]
                    d["cash_eur"] += imported * buy - exported * sell
                    d["wear_eur"] += discharged * settings.wear_eur_per_kwh
                    d["grid_import_kwh"] += imported
                    d["grid_export_kwh"] += exported
                    d["battery_discharge_kwh"] += discharged
                    d["grid_charge_kwh"] += grid
                    d["pv_charge_kwh"] += pvcharge
                    d["site_load_kwh"] += load * duration
                    d["ev_blocked_hours"] += duration if blocked else 0
                    d["battery_discharge_during_ev_kwh"] += discharged if blocked else 0
                    d["peak_import_kw"] = max(d["peak_import_kw"], imported / duration)
                    d["reserve_violations"] += int(
                        energy[p] < capacity * settings.reserve_percent / 100 - 1e-7
                    )
                daily[p]["held_eligible_demand_kwh"] += held_energy
                if trace:
                    interval_records.append(
                        {
                            "timestamp": now.isoformat(),
                            "policy": p,
                            "soc_before": interval_initial / capacity * 100,
                            "soc_after": energy[p] / capacity * 100,
                            "action": "charge"
                            if charge
                            else "self_consume"
                            if supply
                            else "hold",
                            "load_kw": load_energy * 4,
                            "solar_kw": solar_energy * 4,
                            "ev_blocked_minutes": ev_hours * 60,
                            "held_eligible_demand_kwh": held_energy,
                            "cash_eur": daily[p]["cash_eur"] - cash_before,
                            "import_eur_kwh": buy,
                            "export_eur_kwh": sell,
                            "forecast_next_load_kw": slots[0].load_kw
                            if slots
                            else None,
                            "forecast_next_pv_kw": slots[0].solar_kw if slots else None,
                        }
                    )
            # New schedules become active next quarter, not retrospectively.
        for p in policies:
            record = {
                "day": str(day),
                "policy": p,
                **dict(daily[p]),
                "ending_soc_percent": energy[p] / capacity * 100,
            }
            records.append(record)
            for k, v in daily[p].items():
                if k == "peak_import_kw":
                    totals[p][k] = max(totals[p][k], v)
                else:
                    totals[p][k] += v
        progress = {
            "last_day": str(day),
            "days": len(records) // len(policies),
            "cash_eur": {p: totals[p]["cash_eur"] for p in policies},
            "failures": {p: dict(failures[p]) for p in policies},
            "solve_seconds": {p: sum(solve_times[p]) for p in policies},
        }
        (ROOT / "analysis/output" / f"emhass_benchmark_{tag}_progress.json").write_text(
            json.dumps(progress, indent=2)
        )
        print(
            json.dumps(
                {
                    "day": str(day),
                    "cash": {p: round(daily[p]["cash_eur"], 3) for p in policies},
                }
            ),
            flush=True,
        )
    if previous is not None:
        for p in policies:
            totals[p]["terminal_credit_eur"] += (
                (energy[p] - initial) * settings.discharge_efficiency * 0.10
            )
    summary = {}
    for p in policies:
        t = dict(totals[p])
        t["economic_eur"] = (
            t.get("cash_eur", 0)
            + t.get("wear_eur", 0)
            - t.get("terminal_credit_eur", 0)
        )
        t["failures"] = dict(failures[p])
        times = solve_times[p]
        t["solve_seconds_total"] = sum(times)
        t["solve_seconds_p95"] = float(np.quantile(times, 0.95)) if times else 0
        summary[p] = t
    report = {
        "start": str(start),
        "end": str(end),
        "period": period,
        "load_method": load_method,
        "pv_method": pv_method,
        "ev_mode": ev_mode,
        "days": len(records) // len(policies),
        "segments": segments,
        "settings": settings.model_dump(mode="json"),
        "policies": summary,
        "skipped": skips,
        "limitations": [
            "EV sessions inferred; no dedicated EV meter. Complete-session labels score interlock timing only, never forecast inputs.",
            "Ideal atomic next-quarter schedule application; API write jitter and failures not simulated.",
            "Historic HDO09:00–13:00 assumed, verified only2026-10-20 to26.",
            "Battery model and wear assumptions are not independent physical savings measurements.",
        ],
    }
    prefix = ROOT / "analysis/output" / f"emhass_benchmark_{tag}"
    prefix.with_suffix(".json").write_text(json.dumps(report, indent=2))
    if trace:
        prefix.with_name(prefix.name + "_trace.json").write_text(
            json.dumps(interval_records, indent=2)
        )
    if records:
        keys = sorted(set().union(*(r.keys() for r in records)))
        with prefix.with_suffix(".csv").open("w") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(records)
    print(json.dumps(report, indent=2), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-10-12")
    parser.add_argument("--end", default="2026-10-18")
    parser.add_argument("--period", default="2026_recent")
    parser.add_argument("--tag", default="pilot")
    parser.add_argument(
        "--load-method", choices=["mean", "ridge", "intraday"], default="mean"
    )
    parser.add_argument(
        "--ev-mode", choices=["inferred", "none", "broad"], default="inferred"
    )
    parser.add_argument("--capacity", type=float, default=14.2)
    parser.add_argument("--trace", action="store_true")
    parser.add_argument(
        "--current-scenarios",
        default=None,
        help="Comma-separated candidate-planner capacity scenarios; default matches physical capacity",
    )
    parser.add_argument("--efficiency", type=float, default=0.95)
    parser.add_argument(
        "--pv-method",
        choices=["weather", "baseline_cached", "emhass"],
        default="weather",
    )
    parser.add_argument("--wear", type=float, default=0.045)
    parser.add_argument(
        "--policies", default="current,emhass,once_current,old_once,native,no_battery"
    )
    args = parser.parse_args()
    settings = type(load_settings()).model_validate(
        {
            **load_settings().model_dump(),
            "effective_capacity_kwh": args.capacity,
            "capacity_scenarios_kwh": [
                float(x) for x in args.current_scenarios.split(",")
            ]
            if args.current_scenarios
            else [args.capacity],
            "wear_eur_per_kwh": args.wear,
            "charge_efficiency": args.efficiency,
            "discharge_efficiency": args.efficiency,
        }
    )
    evaluate(
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
        args.period,
        settings,
        args.policies.split(","),
        args.load_method,
        args.ev_mode,
        args.tag,
        args.pv_method,
        args.trace,
    )
