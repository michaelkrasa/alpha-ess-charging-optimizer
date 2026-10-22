"""Causal household walk-forward evaluation; never constructs a device client.

Measured load/PV are used only to score future outcomes. Forecast training uses
completed earlier days. All policies share one physical simulator and battery.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import logging
from pathlib import Path
import statistics
import sys
import time as timer

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.manager.domain import UTC, PRAGUE, Slot, Telemetry, day_slots
from src.manager.planner import optimize
from src.manager.settings import load_settings
from src.manager.tariff import prices

RAW = ROOT / "data/backtests"


@dataclass
class Day:
    day: date
    load: np.ndarray
    solar: np.ndarray
    imports: np.ndarray
    exports: np.ndarray
    raw_load: list[list[float]]
    raw_solar: list[list[float]]
    spot: np.ndarray | None = None
    resolution_minutes: int = 15


def integrate_day(day, rows):
    if len(day_slots(day)) != 96:
        raise ValueError("DST transition: offsetless samples excluded")
    points = []
    buckets_load = [[] for _ in range(96)]
    buckets_solar = [[] for _ in range(96)]
    for row in rows:
        stamp = datetime.fromisoformat(row["timestamp"])
        seconds = stamp.hour * 3600 + stamp.minute * 60 + stamp.second
        values = np.array(
            [row[k] for k in ("load_kw", "pv_kw", "grid_import_kw", "grid_export_kw")],
            dtype=float,
        )
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError("Invalid measured power")
        points.append((seconds, values))
        buckets_load[seconds // 900].append(values[0])
        buckets_solar[seconds // 900].append(values[1])
    points.sort(key=lambda pair: pair[0])
    if len(points) < 200 or points[0][0] > 600 or 86400 - points[-1][0] > 600:
        raise ValueError("Insufficient boundary coverage")
    if max(b[0] - a[0] for a, b in zip(points, points[1:])) > 900:
        raise ValueError("Measured gap longer than 15 minutes")
    # Piecewise constant integration. Extend nearest boundary reading <=10min.
    points[0] = (0, points[0][1])
    energy = np.zeros((96, 4))
    for (start, value), (end, _) in zip(points, points[1:] + [(86400, points[-1][1])]):
        while start < end:
            slot = start // 900
            stop = min(end, (slot + 1) * 900)
            energy[slot] += value * (stop - start) / 3600
            start = stop
    if any(not b for b in buckets_load):
        raise ValueError("Quarter hour has no source samples")
    return Day(day, *[energy[:, i] * 4 for i in range(4)], buckets_load, buckets_solar)


def load_history():
    grouped = {}
    with (ROOT / "analysis/output/alphaess_power_2025.csv").open() as source:
        for row in csv.DictReader(source):
            grouped.setdefault(date.fromisoformat(row["timestamp"][:10]), []).append(
                row
            )
    for path in sorted((RAW / "household").glob("*.json")):
        try:
            date.fromisoformat(path.stem)
        except ValueError:
            # Read-only commissioning diagnostics may sit beside dated replay
            # fixtures; they are not part of the canonical history contract.
            continue
        content = json.loads(path.read_text())
        rows = []
        for r in content["samples"]:
            try:
                rows.append(
                    {
                        "timestamp": r["uploadTime"],
                        "load_kw": float(r["load"]) / 1000,
                        "pv_kw": float(r["ppv"]) / 1000,
                        "grid_import_kw": float(r["gridCharge"]) / 1000,
                        "grid_export_kw": float(r["feedIn"]) / 1000,
                    }
                )
            except (ValueError, TypeError, KeyError):
                continue
        grouped[date.fromisoformat(content["day"])] = rows
    days = {}
    excluded = {}
    for day, rows in sorted(grouped.items()):
        try:
            days[day] = integrate_day(day, rows)
        except ValueError as exc:
            excluded[str(day)] = str(exc)
    price_groups = {}
    with (ROOT / "analysis/output/ote_prices_2025.csv").open() as source:
        for row in csv.DictReader(source):
            stamp = datetime.fromisoformat(row["timestamp"])
            price_groups.setdefault(stamp.astimezone(PRAGUE).date(), []).append(
                (stamp.astimezone(UTC), float(row["price_eur_per_mwh"]))
            )
    for day, record in days.items():
        cached = RAW / "prices" / f"{day}.json"
        pairs = price_groups.get(day)
        if pairs:
            pairs = sorted(pairs)
            values = np.array([p for _, p in pairs])
        elif cached.exists():
            values = np.array(json.loads(cached.read_text()), dtype=float)
        else:
            continue
        if len(values) == 24:
            record.spot = np.repeat(values, 4)
            record.resolution_minutes = 60
        elif len(values) == 96:
            record.spot = values
    return days, excluded


def load_fx():
    import httpx

    result = {}
    cache = RAW / "fx"
    cache.mkdir(parents=True, exist_ok=True)
    for year in (2024, 2025, 2026):
        path = cache / f"{year}.txt"
        if not path.exists():
            response = httpx.get(
                "https://www.cnb.cz/en/financial_markets/foreign_exchange_market/exchange_rate_fixing/year.txt",
                params={"year": year},
                timeout=30,
            )
            response.raise_for_status()
            path.write_text(response.text)
        lines = path.read_text().splitlines()
        column = lines[0].split("|").index("1 EUR")
        for row in lines[1:]:
            values = row.split("|")
            if len(values) > column:
                result[datetime.strptime(values[0], "%d.%m.%Y").date()] = float(
                    values[column]
                )
    return result


class Exchange:
    def __init__(self, rates):
        self.rates = rates
        self.dates = sorted(rates)

    def get(self, day, before=False):
        when = day - timedelta(days=1) if before else day
        i = bisect_right(self.dates, when) - 1
        if i < 0 or (when - self.dates[i]).days > 10:
            raise ValueError("No causal EUR conversion rate")
        return self.rates[self.dates[i]]


class Forecaster:
    def __init__(self, days, kind, load_method="mean"):
        self.days = days
        self.load_method = load_method
        self.kind = kind
        self.profile_cache = {}
        self.weather_cache = {}
        self.scale_cache = {}
        self.scale_source = {}

    def profiles(self, day):
        if day in self.profile_cache:
            return self.profile_cache[day]
        past = [self.days.get(day - timedelta(days=i)) for i in range(1, 8)]
        past = [d for d in past if d is not None]
        if len(past) < 6:
            raise ValueError("Warm-up: fewer than six complete prior days")
        load = (
            np.mean([d.load for d in past], axis=0)
            if self.load_method == "mean"
            else np.array(
                [
                    statistics.median([x for d in past for x in d.raw_load[i]])
                    for i in range(96)
                ]
            )
        )
        solar = np.array(
            [
                statistics.median([x for d in past for x in d.raw_solar[i]]) * 0.8
                for i in range(96)
            ]
        )
        self.profile_cache[day] = load, solar
        return load, solar

    def weather(self, run):
        key = run.strftime("%Y-%m-%dT%H")
        if key not in self.weather_cache:
            path = RAW / "weather" / f"{key}.json"
            if not path.exists():
                raise ValueError("Archived weather run missing")
            payload = json.loads(path.read_text())
            hourly = payload["hourly"]
            self.weather_cache[key] = {
                datetime.fromisoformat(t).replace(tzinfo=UTC): float(g)
                for t, g in zip(hourly["time"], hourly["global_tilted_irradiance"])
                if g is not None
            }
        return self.weather_cache[key]

    @staticmethod
    def available_run(now):
        before = now.astimezone(UTC) - timedelta(hours=6)
        return before.replace(
            hour=(before.hour // 6) * 6, minute=0, second=0, microsecond=0
        )

    def _scale_from_previous_day(self, day):
        previous = self.days.get(day - timedelta(days=1))
        if previous is None:
            raise ValueError("PV calibration day unavailable")
        start = day_slots(previous.day)[0]
        weather = self.weather(self.available_run(start))
        samples = []
        for i, t in enumerate(day_slots(previous.day)):
            # GTI timestamp is the end of the preceding-hour averaging period.
            key = t.replace(minute=0) + timedelta(hours=1)
            g = weather.get(key, 0)
            if g >= 200:
                samples.extend(pv * 1000 / g for pv in previous.raw_solar[i] if pv > 0)
        if len(samples) < 6:
            raise ValueError("Insufficient causal PV calibration samples")
        value = statistics.median(samples)
        return value

    def scale(self, day):
        if day in self.scale_cache:
            return self.scale_cache[day]
        # Match the live bounded-age cloudy-day fallback. Each candidate uses
        # only history available on its own calibration date; never renew the
        # date of an old calibration by repeatedly falling back to it.
        for age in range(8):
            calibration_day = day - timedelta(days=age)
            try:
                value = self._scale_from_previous_day(calibration_day)
            except (ValueError, KeyError):
                continue
            self.scale_cache[day] = value
            self.scale_source[day] = calibration_day
            return value
        raise ValueError("No causal PV calibration within seven days")

    def forecast(self, now, instants):
        day = now.astimezone(PRAGUE).date()
        load, persistent = self.profiles(day)
        keys = [
            t.astimezone(PRAGUE).hour * 4 + t.astimezone(PRAGUE).minute // 15
            for t in instants
        ]
        loads = load[keys]
        if self.kind == "persistence":
            return loads, persistent[keys]
        weather = self.weather(self.available_run(now))
        scale = self.scale(day)
        solar = []
        for t in instants:
            key = t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            if key not in weather:
                raise ValueError("Archived forecast horizon incomplete")
            solar.append(max(0, weather[key]) * scale * 0.8 / 1000)
        return loads, np.array(solar)


@dataclass
class Flow:
    energy: float
    grid_import: float
    grid_export: float
    discharge: float
    grid_charge: float
    pv_charge: float


def physical_step(energy, load, pv, settings, action="native", power=0, target=100):
    """One quarter hour: greedy PV capture and native household discharge."""
    capacity = settings.effective_capacity_kwh or 17.2
    floor = settings.reserve_percent / 100 * capacity
    ceiling = settings.maximum_percent / 100 * capacity
    deficit = max(0, load - pv)
    surplus = max(0, pv - load)
    pvcharge = min(
        surplus,
        settings.charge_kw,
        max(0, (ceiling - energy) / (0.25 * settings.charge_efficiency)),
    )
    energy += pvcharge * 0.25 * settings.charge_efficiency
    gridcharge = 0.0
    discharge = 0.0
    if action in ("charge", "hold"):
        watts = max(0.1, power) if action == "charge" else 0.1
        target_energy = int(target) / 100 * capacity
        gridcharge = max(
            0,
            min(
                watts - pvcharge,
                settings.charge_kw - pvcharge,
                settings.charge_kw
                if settings.import_limit_kw is None
                else settings.import_limit_kw - deficit,
                (target_energy - energy) / (0.25 * settings.charge_efficiency),
            ),
        )
        energy += gridcharge * 0.25 * settings.charge_efficiency
    elif action == "native":
        discharge = max(
            0,
            min(
                deficit,
                settings.discharge_kw,
                (energy - floor) * settings.discharge_efficiency / 0.25,
            ),
        )
        energy -= discharge * 0.25 / settings.discharge_efficiency
    return Flow(
        energy,
        (deficit + gridcharge - discharge) * 0.25,
        (surplus - pvcharge) * 0.25,
        discharge * 0.25,
        gridcharge * 0.25,
        pvcharge * 0.25,
    )


def mapped_action(decision):
    stamp = datetime.fromisoformat(decision.timestamp).astimezone(PRAGUE)
    end = (stamp.astimezone(UTC) + timedelta(minutes=15)).astimezone(PRAGUE)
    if (
        stamp.date() != end.date()
        or stamp.fold
        or end.fold
        or end.hour == 23
        and end.minute > 45
    ):
        return ("native", 0, 100)
    if decision.action == "charge":
        return (
            "charge",
            max(0.1, round(decision.grid_charge_kw * 1000) / 1000),
            int(decision.soc_percent),
        )
    if decision.action == "hold":
        return ("hold", 0.1, 10)
    return ("native", 0, 100)


def legacy_planner(settings):
    # Reuse the actual pure legacy analysis functions, without constructing its API client.
    from src.optimizer import ESSOptimizer
    from src.battery_manager import BatteryManager
    from src.price_analyzer import PriceAnalyzer
    import yaml

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    old = object.__new__(ESSOptimizer)
    old.config = cfg
    old.solar_config = None
    old.battery_manager = BatteryManager(
        cfg["charge_rate_kw"], cfg["avg_day_load_kw"], 10, 100
    )
    # Original API integration multiplied nominal capacity by usCapacity90%.
    old.battery_manager.set_capacity(
        settings.legacy_reference_capacity_kwh or 17.2 * 0.9
    )
    old.price_analyzer = PriceAnalyzer(
        cfg["price_multiplier"], cfg["min_window_slots"], cfg["smoothing_window"]
    )
    old.MIN_WINDOW_SLOTS = cfg["min_window_slots"]
    old.DISCHARGE_EXTENSION_THRESHOLD = cfg["discharge_extension_threshold"]
    old.DAYTIME_CHARGE_FACTOR = cfg["daytime_charge_factor"]
    return old


def make_slots(now, days, forecaster, exchange, tariff):
    local = now.astimezone(PRAGUE)
    day = local.date()
    available = [day] + ([day + timedelta(days=1)] if local.hour >= 15 else [])
    instants = []
    spot = []
    for d in available:
        if d not in days or days[d].spot is None:
            continue
        for t, p in zip(day_slots(d), days[d].spot):
            if t > now:
                instants.append(t)
                spot.append(p)
    if len(instants) < 4:
        raise ValueError("Published horizon shorter than one hour")
    load, solar = forecaster.forecast(now, instants)
    past = [days.get(day - timedelta(days=i)) for i in range(1, 8)]
    past = [d for d in past if d is not None]
    ceilings = [
        max(float(d.load[(i + j) % 96]) for d in past for j in range(-2, 3))
        for i in range(96)
    ]
    fx = exchange.get(day, before=local.hour < 15)
    result = []
    for t, p, l, s in zip(instants, spot, load, solar):
        h = (
            tariff == "vt"
            or tariff == "schedule"
            and 9 <= t.astimezone(PRAGUE).hour < 13
        )
        buy, sell = prices(float(p), fx, h)
        result.append(
            Slot(
                t.isoformat(),
                float(p),
                buy,
                sell,
                float(l),
                float(s),
                "VT" if h else "NT",
                ceilings[
                    t.astimezone(PRAGUE).hour * 4 + t.astimezone(PRAGUE).minute // 15
                ],
            )
        )
    return result


def validate_daily_decision_minute(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 900 <= value <= 1439
    ):
        raise ValueError("Daily decision minute must be an integer from 900 to 1439")
    return value


def daily_decision_due(now, last_write, decision_minute=900):
    """Fixed local-time sensitivity, retaining a 24-hour daily API interval."""
    validate_daily_decision_minute(decision_minute)
    local = now.astimezone(PRAGUE)
    return local.hour * 60 + local.minute >= decision_minute and (
        last_write is None or (now - last_write).total_seconds() >= 86400
    )


def evaluate(
    days,
    exchange,
    settings,
    start,
    end,
    forecast_kind="persistence",
    tariff="schedule",
    policies=None,
    load_method="mean",
    daily_decision_minute=900,
):
    validate_daily_decision_minute(daily_decision_minute)
    capacity = settings.effective_capacity_kwh or 17.2
    initial = capacity * 0.5
    logging.disable(logging.CRITICAL)
    forecaster = Forecaster(days, forecast_kind, load_method=load_method)
    old = legacy_planner(settings)
    policies = policies or ["native", "legacy", "rolling", "daily"]
    energy = {p: initial for p in policies}
    totals = {p: {} for p in policies}
    records = []
    previous = None
    skips = {}
    segments = 0
    pending = {}
    daily_commands = {}
    standard_schedule, standard_pending, standard_last_write = {}, {}, {}
    standard_last_success = {}
    standard_decision_day = {}
    legacy_modes = []
    forecast_failures = {}
    solver_times = []
    forecast_errors = []
    standard_diagnostics = {
        p: {"replans": 0, "schedule_changes": 0, "failures": {}}
        for p in policies
        if p
        in {
            "standard_daily",
            "standard_once_daily",
            "standard_rolling",
            "legacy_matched",
            "charge_only",
        }
    }
    terminal_price = (
        0.10  # Prespecified EUR/kWh of usable stored energy for fair scoring.
    )
    eligible = []
    for day in sorted(d for d in days if start <= d <= end):
        if days[day].spot is None:
            skips[str(day)] = "Published prices missing"
            continue
        try:
            forecaster.profiles(day)
        except ValueError as exc:
            skips[str(day)] = str(exc)
            continue
        if forecast_kind == "weather":
            # Archive holes are evaluation gaps, not evidence that the actual
            # operational forecast service failed on that historical day.
            needed = {forecaster.available_run(t) for t in day_slots(day)}
            needed.add(forecaster.available_run(day_slots(day - timedelta(days=1))[0]))
            try:
                for run in needed:
                    forecaster.weather(run)
            except (ValueError, KeyError):
                skips[str(day)] = (
                    "Archived IFS run unavailable; excluded rather than fabricated"
                )
                continue
        eligible.append(day)
    for day_index, day in enumerate(eligible):
        actual = days[day]
        if previous is None or day != previous + timedelta(days=1):
            if previous is not None:
                for p in policies:
                    totals[p]["terminal_credit_eur"] = (
                        totals[p].get("terminal_credit_eur", 0)
                        + (energy[p] - initial)
                        * settings.discharge_efficiency
                        * terminal_price
                    )
            energy = {p: initial for p in policies}
            pending = {}
            daily_commands = {}
            standard_schedule, standard_pending, standard_last_write = {}, {}, {}
            standard_last_success = {}
            standard_decision_day = {}
            segments += 1
        start_stamp = day_slots(day)[0]
        fx = exchange.get(day)
        previous = day
        if "legacy" in policies:
            # Production legacy code continues price-only planning if its optional
            # solar forecast fails. Do not handicap that baseline with a shutdown.
            solar_hint = None
            try:
                _, s = forecaster.forecast(start_stamp, day_slots(day))
                solar_hint = dict(enumerate(s * 0.25))
            except ValueError:
                pass
            try:
                legacy = old.analyze_day(
                    dict(enumerate(actual.spot)),
                    energy["legacy"] / capacity * 100,
                    solar_hint,
                )
                cycles = legacy.cycles[:2]
                legacy_modes = []
                for i in range(96):
                    charge = (
                        any(
                            c.charge_window.start_slot <= i < c.charge_window.end_slot
                            for c in cycles
                        )
                        and i < 95
                    )
                    discharge = (
                        any(
                            c.discharge_window.start_slot
                            <= i
                            < c.discharge_window.end_slot
                            for c in cycles
                        )
                        and i < 95
                    )
                    legacy_modes.append(
                        ("charge", 5, 100)
                        if charge
                        else ("native", 0, 100)
                        if discharge or not cycles
                        else ("hold", 0.1, 10)
                    )
            except ValueError:
                legacy_modes = [("native", 0, 100)] * 96
        # Daily planner has same information and forecast at midnight, then no replanning.
        if "daily" in policies:
            try:
                horizon = make_slots(start_stamp, days, forecaster, exchange, tariff)
                state = Telemetry(
                    start_stamp.isoformat(),
                    energy["daily"] / capacity * 100,
                    capacity,
                    0,
                    0,
                    0,
                    0,
                )
                daily_commands = {
                    d.timestamp: mapped_action(d)
                    for d in optimize(horizon, state, settings)
                }
            except ValueError:
                daily_commands = {}
        sums = {
            p: {
                "cash_eur": 0.0,
                "wear_eur": 0.0,
                "grid_import_kwh": 0.0,
                "grid_export_kwh": 0.0,
                "battery_discharge_kwh": 0.0,
                "grid_charge_kwh": 0.0,
                "pv_charge_kwh": 0.0,
                "reserve_violations": 0,
                "import_overload_intervals": 0,
                "minimum_soc_percent": 100.0,
                "maximum_soc_percent": 0.0,
                "peak_import_kw": 0.0,
            }
            for p in policies
        }
        fallbacks = 0
        for i, now in enumerate(day_slots(day)):
            buy, sell = prices(
                float(actual.spot[i]),
                fx,
                tariff == "vt"
                or tariff == "schedule"
                and 9 <= now.astimezone(PRAGUE).hour < 13,
            )
            if "rolling" in policies:
                action = pending.pop(now.isoformat(), ("native", 0, 100))
                try:
                    horizon = make_slots(now, days, forecaster, exchange, tariff)
                    state = Telemetry(
                        now.isoformat(),
                        energy["rolling"] / capacity * 100,
                        capacity,
                        float(actual.load[i - 1] if i else 0),
                        float(actual.solar[i - 1] if i else 0),
                        0,
                        0,
                    )
                    t = timer.perf_counter()
                    decision = optimize(horizon, state, settings)[0]
                    solver_times.append(timer.perf_counter() - t)
                    pending[decision.timestamp] = mapped_action(decision)
                    target = datetime.fromisoformat(decision.timestamp)
                    target_day = target.astimezone(PRAGUE).date()
                    index = (
                        target.astimezone(PRAGUE).hour * 4
                        + target.astimezone(PRAGUE).minute // 15
                    )
                    if target_day in days:
                        forecast_errors.append(
                            (
                                decision.load_kw - float(days[target_day].load[index]),
                                decision.solar_kw
                                - float(days[target_day].solar[index]),
                            )
                        )
                except ValueError as exc:
                    forecast_failures[str(exc)] = forecast_failures.get(str(exc), 0) + 1
                    fallbacks += 1
            from src.manager.daily import plan_daily, in_window, step as standard_step
            from src.manager.daily_charge_only import plan_daily as charge_only_plan

            for p in (
                "standard_daily",
                "standard_once_daily",
                "standard_rolling",
                "legacy_matched",
                "charge_only",
            ):
                if p not in policies:
                    continue
                pending_plan = standard_pending.get(p)
                if (
                    pending_plan
                    and datetime.fromisoformat(pending_plan["valid_from"]) <= now
                ):
                    standard_schedule[p] = standard_pending.pop(p)
                last = standard_last_write.get(p)
                local = now.astimezone(PRAGUE)
                once_due = (
                    local.hour * 60 + local.minute >= daily_decision_minute
                    and standard_decision_day.get(p) != local.date()
                )
                due = (
                    True
                    if p == "standard_rolling"
                    else once_due
                    if p == "standard_once_daily"
                    else daily_decision_due(now, last, daily_decision_minute)
                )
                if due:
                    standard_decision_day[p] = local.date()
                    try:
                        standard_diagnostics[p]["replans"] += 1
                        horizon = make_slots(now, days, forecaster, exchange, tariff)
                        state = Telemetry(
                            now.isoformat(),
                            energy[p] / capacity * 100,
                            capacity,
                            0,
                            0,
                            0,
                            0,
                        )
                        proposed = (
                            charge_only_plan(horizon, state, settings)
                            if p == "charge_only"
                            else plan_daily(
                                horizon,
                                state,
                                settings,
                                baseline_only=p == "legacy_matched",
                                **(
                                    {
                                        "minimum_slots": 4,
                                        "current_schedule": standard_schedule.get(p),
                                    }
                                    if p in {"standard_rolling", "standard_once_daily"}
                                    else {}
                                ),
                            )
                        )
                        stored = standard_schedule.get(p, {})
                        standard_last_success[p] = now
                        if any(
                            proposed.get(k) != stored.get(k)
                            for k in ("charge", "discharge")
                        ):
                            standard_pending[p] = proposed
                            standard_last_write[p] = now
                            standard_diagnostics[p]["schedule_changes"] += 1
                    except ValueError as exc:
                        failures = standard_diagnostics[p]["failures"]
                        failures[str(exc)] = failures.get(str(exc), 0) + 1
                        if p == "standard_rolling":
                            last_success = standard_last_success.get(p)
                            if (
                                last_success is None
                                or (now - last_success).total_seconds()
                                > settings.plan_grace_minutes * 60
                            ):
                                # Live failure handling restores native after
                                # the grace period; it does not retain stale
                                # repeating wall-clock schedules indefinitely.
                                standard_pending.pop(p, None)
                                standard_schedule.pop(p, None)
            for policy in policies:
                command = (
                    ("native", 0, 100)
                    if policy == "native"
                    else legacy_modes[i]
                    if policy == "legacy"
                    else daily_commands.get(now.isoformat(), ("native", 0, 100))
                    if policy == "daily"
                    else action
                    if policy == "rolling"
                    else ("native", 0, 100)
                )
                if policy in {
                    "standard_daily",
                    "standard_once_daily",
                    "standard_rolling",
                    "legacy_matched",
                    "charge_only",
                }:
                    periods = []
                    target = 100
                    if policy in standard_schedule:
                        c = standard_schedule[policy]["charge"]
                        if c["gridCharge"]:
                            periods = [
                                (c[f"timeChaf{j}"], c[f"timeChae{j}"]) for j in (1, 2)
                            ]
                        target = c["batHighCap"]
                    flow = Flow(
                        *standard_step(
                            energy[policy],
                            float(actual.load[i]),
                            float(actual.solar[i]),
                            in_window(now, periods),
                            target,
                            capacity,
                            settings,
                            discharge=(
                                policy not in standard_schedule
                                or standard_schedule[policy]
                                .get("discharge", {})
                                .get("ctrDis", 0)
                                == 0
                                or in_window(
                                    now,
                                    [
                                        (
                                            standard_schedule[policy]["discharge"][
                                                f"timeDisf{j}"
                                            ],
                                            standard_schedule[policy]["discharge"][
                                                f"timeDise{j}"
                                            ],
                                        )
                                        for j in (1, 2)
                                    ],
                                )
                            ),
                        )
                    )
                elif policy == "legacy":
                    # Same fixed-power, no-software-headroom-clipping physics
                    # as the new standard API. Hold is discharge time control.
                    flow = Flow(
                        *standard_step(
                            energy[policy],
                            float(actual.load[i]),
                            float(actual.solar[i]),
                            command[0] == "charge",
                            100,
                            capacity,
                            settings,
                            discharge=command[0] == "native",
                        )
                    )
                else:
                    flow = physical_step(
                        energy[policy],
                        float(actual.load[i]),
                        float(actual.solar[i]),
                        settings,
                        *command,
                    )
                energy[policy] = flow.energy
                row = sums[policy]
                row["cash_eur"] += flow.grid_import * buy - flow.grid_export * sell
                row["wear_eur"] += flow.discharge * settings.wear_eur_per_kwh
                for key, value in [
                    ("grid_import_kwh", flow.grid_import),
                    ("grid_export_kwh", flow.grid_export),
                    ("battery_discharge_kwh", flow.discharge),
                    ("grid_charge_kwh", flow.grid_charge),
                    ("pv_charge_kwh", flow.pv_charge),
                ]:
                    row[key] += value
                soc = flow.energy / capacity * 100
                row["minimum_soc_percent"] = min(row["minimum_soc_percent"], soc)
                row["maximum_soc_percent"] = max(row["maximum_soc_percent"], soc)
                row["reserve_violations"] += int(soc < 10 - 1e-6)
                row["import_overload_intervals"] += int(
                    settings.import_limit_kw is not None
                    and flow.grid_import * 4 > settings.import_limit_kw + 1e-6
                )
                row["peak_import_kw"] = max(row["peak_import_kw"], flow.grid_import * 4)
        for policy, row in sums.items():
            row["policy"] = policy
            row["day"] = str(day)
            row["ending_soc_percent"] = energy[policy] / capacity * 100
            row["fallback_intervals"] = fallbacks if policy == "rolling" else 0
            row["observed_load_kwh"] = sum(actual.load) * 0.25
            row["observed_solar_kwh"] = sum(actual.solar) * 0.25
            records.append(row)
            for key, value in row.items():
                if isinstance(value, (int, float)) and key not in (
                    "minimum_soc_percent",
                    "maximum_soc_percent",
                    "ending_soc_percent",
                    "peak_import_kw",
                ):
                    totals[policy][key] = totals[policy].get(key, 0) + value
            totals[policy]["peak_import_kw"] = max(
                totals[policy].get("peak_import_kw", 0), row["peak_import_kw"]
            )
        if day_index % 14 == 0:
            print(
                f"{forecast_kind}/{tariff}: {day_index + 1}/{len(eligible)} days through {day}",
                flush=True,
            )
    for p in policies:
        totals[p]["terminal_credit_eur"] = (
            totals[p].get("terminal_credit_eur", 0)
            + (energy[p] - initial) * settings.discharge_efficiency * terminal_price
        )
        totals[p]["economic_cost_eur"] = (
            totals[p]["cash_eur"]
            + totals[p]["wear_eur"]
            - totals[p]["terminal_credit_eur"]
        )
        totals[p]["ending_soc_percent"] = energy[p] / capacity * 100
        totals[p]["equivalent_usable_cycles"] = totals[p]["battery_discharge_kwh"] / (
            capacity * 0.9
        )
    return {
        "start": str(start),
        "end": str(end),
        "days": len(eligible),
        "segments": segments,
        "forecast": forecast_kind,
        "daily_decision_minute": daily_decision_minute,
        "daily_timing_model": "fixed local decision time; atomic next-quarter application; no failed-write simulation",
        "standard_rolling_timing_model": "replan every quarter from current simulated SOC and installed pair; minimum four known-price slots; atomic next-quarter application; no failed-write simulation",
        "standard_once_daily_timing_model": "same planner contract as standard_rolling, but one decision attempt per day at fixed local decision time, even if pair is unchanged; atomic next-quarter application",
        "standard_diagnostics": standard_diagnostics,
        "pv_calibration_model": "previous-day irradiance scaling, causal calibration retained at most seven days on dark days",
        "tariff_scenario": tariff,
        "import_limit_kw": settings.import_limit_kw,
        "totals": totals,
        "excluded": skips,
        "forecast_failures": forecast_failures,
        "solver_seconds_p50": float(np.median(solver_times)) if solver_times else None,
        "solver_seconds_p95": float(np.percentile(solver_times, 95))
        if solver_times
        else None,
        "forecast_load_mae_kw": float(np.mean(np.abs(np.array(forecast_errors)[:, 0])))
        if forecast_errors
        else None,
        "forecast_solar_mae_kw": float(np.mean(np.abs(np.array(forecast_errors)[:, 1])))
        if forecast_errors
        else None,
    }, records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2025, 1, 8))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument(
        "--forecast", choices=["persistence", "weather"], default="persistence"
    )
    parser.add_argument(
        "--tariff", choices=["schedule", "nt", "vt"], default="schedule"
    )
    parser.add_argument("--wear", type=float, default=0.045)
    parser.add_argument(
        "--policies",
        nargs="+",
        choices=[
            "native",
            "legacy",
            "rolling",
            "daily",
            "standard_daily",
            "standard_once_daily",
            "standard_rolling",
            "legacy_matched",
            "charge_only",
        ],
    )
    parser.add_argument("--capacity", type=float)
    parser.add_argument("--load-method", choices=["mean", "median"], default="mean")
    parser.add_argument(
        "--daily-decision-minute",
        type=lambda value: validate_daily_decision_minute(int(value)),
        default=900,
        metavar="900..1439",
        help="Earliest local minute for daily-policy decisions (900=15:00); price publication remains 15:00",
    )
    parser.add_argument("--output", default="analysis/output/backtest")
    args = parser.parse_args()
    settings = load_settings().model_copy(update={"wear_eur_per_kwh": args.wear})
    if args.capacity is not None:
        settings = settings.model_copy(update={"effective_capacity_kwh": args.capacity})
    days, excluded = load_history()
    exchange = Exchange(load_fx())
    summary, records = evaluate(
        days,
        exchange,
        settings,
        args.start,
        args.end,
        args.forecast,
        args.tariff,
        args.policies,
        args.load_method,
        args.daily_decision_minute,
    )
    summary["data_exclusions"] = excluded
    summary["load_method"] = args.load_method
    summary["daily_policy_version"] = 2
    summary["plant_assumptions"] = {
        "effective_capacity_kwh": settings.effective_capacity_kwh or 17.2,
        "charge_kw": settings.charge_kw,
        "discharge_kw": settings.discharge_kw,
        "charge_efficiency": settings.charge_efficiency,
        "discharge_efficiency": settings.discharge_efficiency,
        "wear_eur_kwh": settings.wear_eur_per_kwh,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    with out.with_suffix(".csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
