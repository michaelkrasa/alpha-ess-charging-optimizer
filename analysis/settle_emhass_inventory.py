"""Value ending stock using a common native-only continuation, when observable.

Supplementary scoring only: never changes or selects the preceding decisions.
No forecasts/optimization/device calls. Stop when battery states converge.
"""

import csv
import json
import sys
from datetime import date, timedelta
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analysis.backtest_household import Exchange, load_fx, load_history
from analysis.emhass_benchmark import measurement_segments
from src.manager.daily import step
from src.manager.domain import PRAGUE, day_slots
from src.manager.settings import Settings
from src.manager.tariff import prices


@cache
def inputs():
    return load_history(), measurement_segments(), Exchange(load_fx())


def settle(tag):
    prefix = ROOT / "analysis/output" / f"emhass_benchmark_{tag}"
    report = json.loads(prefix.with_suffix(".json").read_text())
    if not {"current", "emhass"} <= report["policies"].keys():
        return None
    s = Settings.model_validate(report["settings"])
    capacity = s.effective_capacity_kwh
    with prefix.with_suffix(".csv").open() as f:
        rows = list(csv.DictReader(f))
    last = max(r["day"] for r in rows)
    energy = {
        r["policy"]: float(r["ending_soc_percent"]) / 100 * capacity
        for r in rows
        if r["day"] == last and r["policy"] in {"current", "emhass"}
    }
    initial_energy = dict(energy)
    (days, _), measured, fx = inputs()
    cash = {"current": 0.0, "emhass": 0.0}
    wear = dict(cash)
    converged = abs(energy["current"] - energy["emhass"]) < 1e-7
    hours = 0.0
    finished_at = None
    reason = None
    last_day = date.fromisoformat(last)
    for offset in range(1, 8):
        if converged:
            break
        day = last_day + timedelta(days=offset)
        if day not in measured or day not in days or days[day].spot is None:
            reason = "No contiguous measured/priced continuation"
            break
        for i, t in enumerate(day_slots(day)):
            buy, sell = prices(
                float(days[day].spot[i]),
                fx.get(day),
                9 <= t.astimezone(PRAGUE).hour < 13,
            )
            for duration, load, pv, ev in measured[day][i]:
                blocked = ev or (report["ev_mode"] == "broad" and load >= 6)
                if report["ev_mode"] == "none":
                    blocked = False
                for policy in cash:
                    energy[policy], imp, exp, d, _, _ = step(
                        energy[policy],
                        load,
                        pv,
                        False,
                        100,
                        capacity,
                        s,
                        discharge=not blocked,
                        duration_hours=duration,
                    )
                    cash[policy] += imp * buy - exp * sell
                    wear[policy] += d * s.wear_eur_per_kwh
                hours += duration
                if abs(energy["current"] - energy["emhass"]) < 1e-7:
                    converged = True
                    finished_at = (t + timedelta(minutes=15)).isoformat()
                    break
            if converged:
                break
    a, b = report["policies"]["current"], report["policies"]["emhass"]
    terminal_difference = (
        (initial_energy["emhass"] - initial_energy["current"])
        * s.discharge_efficiency
        * 0.10
    )
    result = {
        "benchmark": tag,
        "last_day": last,
        "initial_energy_kwh": initial_energy,
        "converged": converged,
        "continuation_hours": hours,
        "convergence_quarter_end": finished_at,
        "ending_energy_kwh": energy,
        "tail_cash_eur": cash,
        "tail_wear_eur": wear,
        "emhass_saving_cash_eur": a["cash_eur"]
        - b["cash_eur"]
        + cash["current"]
        - cash["emhass"]
        if converged
        else None,
        "emhass_saving_economic_eur": a["economic_eur"]
        - b["economic_eur"]
        - terminal_difference
        + cash["current"]
        - cash["emhass"]
        + wear["current"]
        - wear["emhass"]
        if converged
        else None,
        "unavailable_reason": reason,
        "method": "After evaluation end, both policies use native PV/self-consumption with grid charging off until SOC converges. EV interlock retained; no optimizer sees future actuals. Earlier data-gap segments retain the prespecified terminal credit. This is a supplementary inventory liquidation test, not continued rolling operation or a replacement primary endpoint.",
    }
    (ROOT / "analysis/output" / f"emhass_inventory_{tag}.json").write_text(
        json.dumps(result, indent=2)
    )
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    settle(sys.argv[1] if len(sys.argv) > 1 else "week_mean")
