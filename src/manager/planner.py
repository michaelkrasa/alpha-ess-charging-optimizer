"""Mixed integer optimization of physically separate household energy flows.

Only PV surplus can be exported. Binary modes prevent simultaneous battery
charge/discharge, including when import prices are negative. No EV model.
"""

from datetime import datetime
import math

import numpy as np
import highspy

from .domain import Decision


def optimize(slots, telemetry, settings):
    if not slots:
        raise ValueError("No priced forecast intervals")
    n = len(slots)
    # Each interval has grid charge, PV charge, house discharge, energy, charge mode.
    size = 5 * n
    objective = np.zeros(size)
    lower = np.zeros(size)
    upper = np.full(size, np.inf)
    integer = np.zeros(size)
    rows, lows, highs = [], [], []

    def constrain(terms, low, high):
        rows.append(terms)
        lows.append(low)
        highs.append(high)

    floor = telemetry.capacity_kwh * settings.reserve_percent / 100
    ceiling = telemetry.capacity_kwh * settings.maximum_percent / 100
    initial = telemetry.capacity_kwh * telemetry.soc_percent / 100
    # Below-reserve telemetry can recover, but cannot discharge further.
    effective_floor = min(floor, initial)
    for i, slot in enumerate(slots):
        if any(
            not math.isfinite(v)
            for v in (
                slot.import_eur_kwh,
                slot.export_eur_kwh,
                slot.load_kw,
                slot.solar_kw,
            )
        ):
            raise ValueError("Non-finite prices/forecast")
        if min(slot.load_kw, slot.solar_kw) < 0:
            raise ValueError("Negative load/solar forecast")
        t = datetime.fromisoformat(slot.timestamp)
        if t.tzinfo is None:
            raise ValueError("Forecast timestamp must include timezone")
        if (
            i
            and (t - datetime.fromisoformat(slots[i - 1].timestamp)).total_seconds()
            != 900
        ):
            raise ValueError("Forecast must be contiguous UTC quarter hours")
        net = max(0, slot.load_kw - slot.solar_kw)
        surplus = max(0, slot.solar_kw - slot.load_kw)
        g, p, d, e, z = (5 * i + j for j in range(5))
        objective[g] = slot.import_eur_kwh * 0.25
        objective[p] = slot.export_eur_kwh * 0.25
        objective[d] = (settings.wear_eur_per_kwh - slot.import_eur_kwh) * 0.25
        upper[g] = (
            (
                settings.charge_kw
                if settings.import_limit_kw is None
                else max(0, min(settings.charge_kw, settings.import_limit_kw - net))
            )
            if not surplus
            else 0
        )
        upper[p] = min(surplus, settings.charge_kw)
        upper[d] = min(net, settings.discharge_kw)
        lower[e], upper[e] = effective_floor, ceiling
        upper[z] = 1
        integer[z] = 1
        constrain({g: 1, p: 1, z: -settings.charge_kw}, -np.inf, 0)
        constrain({d: 1, z: settings.discharge_kw}, -np.inf, settings.discharge_kw)
        terms = {
            e: 1,
            g: -0.25 * settings.charge_efficiency,
            p: -0.25 * settings.charge_efficiency,
            d: 0.25 / settings.discharge_efficiency,
        }
        if i:
            terms[e - 5] = -1
            constrain(terms, 0, 0)
        else:
            constrain(terms, initial, initial)
        # Household demand is non-deferrable. Existing overload cannot be fixed by charging.
        if settings.import_limit_kw is not None and net <= settings.import_limit_kw:
            constrain({g: 1, d: -1}, -np.inf, settings.import_limit_kw - net)

    # Avoid emptying the battery merely because the known-price horizon ends.
    # Stored energy has a conservative residual value, net of wear and losses.
    terminal = (
        max(0, min(s.import_eur_kwh for s in slots)) * settings.discharge_efficiency
    )
    objective[5 * (n - 1) + 3] = -max(0, terminal - settings.wear_eur_per_kwh)
    solver = highspy.Highs()
    solver.setOptionValue("output_flag", False)
    solver.setOptionValue("threads", 1)
    solver.setOptionValue("time_limit", 10.0)
    solver.setOptionValue("mip_rel_gap", 0.005)
    solver.addCols(
        size,
        objective,
        lower,
        upper,
        0,
        np.array([], dtype=np.int32),
        np.array([], dtype=np.int32),
        np.array([], dtype=float),
    )
    for i in np.flatnonzero(integer):
        solver.changeColIntegrality(int(i), highspy.HighsVarType.kInteger)
    for terms, low, high in zip(rows, lows, highs):
        solver.addRow(
            low,
            high,
            len(terms),
            np.array(list(terms), dtype=np.int32),
            np.array(list(terms.values()), dtype=float),
        )
    solver.run()
    if solver.getModelStatus() != highspy.HighsModelStatus.kOptimal:
        raise ValueError("Optimizer could not find a valid plan")
    solution = solver.getSolution().col_value
    decisions = []
    for i, slot in enumerate(slots):
        g, p, d, e, _ = solution[i * 5 : i * 5 + 5]
        action = (
            "charge"
            if g > 0.05
            else "self_consume"
            if d > 0.05
            else "solar"
            if p > 0.05
            else "hold"
        )
        reason = {
            "charge": "Buy energy now to avoid more expensive household imports later.",
            "self_consume": "Use stored energy for household demand above the 10% reserve.",
            "solar": "Store surplus solar for later household demand.",
            "hold": "Preserve stored energy for more valuable household consumption later.",
        }[action]
        decisions.append(
            Decision(
                slot.timestamp,
                action,
                round(float(g), 3),
                round(float(d), 3),
                round(float(p), 3),
                round(float(e) / telemetry.capacity_kwh * 100, 2),
                slot.import_eur_kwh,
                slot.export_eur_kwh,
                slot.load_kw,
                slot.solar_kw,
                reason,
            )
        )
    return decisions
