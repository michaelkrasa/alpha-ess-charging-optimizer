from datetime import datetime, timedelta

from src.manager.consumption import household_training_history, separate_grid_only_load
from src.manager.domain import PRAGUE


def history(discharge=False, charging=False):
    start = datetime(2026, 10, 1)
    result = []
    for i in range(24):
        load = 12000 if 6 <= i < 18 else 1000
        battery = 6000 if discharge and load > 6000 else -5000 if charging else 0
        result.append(
            {
                "uploadTime": (start + timedelta(minutes=5 * i)).isoformat(),
                "load": load,
                "ppv": 0,
                "gridCharge": load - battery,
                "feedIn": 0,
                "cbat": 50 + i if charging else 50 - i if discharge else 50,
                "pchargingPile": 0,
            }
        )
    return result


def test_grid_only_ev_does_not_become_household_training_demand():
    raw = history()
    clean = separate_grid_only_load(raw)
    assert len(clean) == len(raw)
    assert all(r["load"] == 1000 for r in clean)
    assert sum(r["ev_grid_only"] for r in clean) == 12
    assert clean[8]["site_load"] == 12000
    assert raw[8]["load"] == 12000


def test_battery_supplied_large_appliance_is_not_removed():
    clean = separate_grid_only_load(history(discharge=True))
    assert not any(r["ev_grid_only"] for r in clean)
    assert clean[8]["load"] == 12000


def test_ev_can_charge_from_grid_while_battery_also_charges():
    assert any(
        r["ev_grid_only"] for r in separate_grid_only_load(history(charging=True))
    )
    assert not any(
        r["ev_grid_only"]
        for r in separate_grid_only_load(history(charging=True), strict_idle=True)
    )


def test_battery_charging_alone_or_brief_load_spike_is_not_ev():
    raw = history(charging=True)
    for r in raw:
        r["load"] = 1000
        r["gridCharge"] = 6000
    assert not any(r["ev_grid_only"] for r in separate_grid_only_load(raw))
    raw = history()[:8]
    assert not any(r["ev_grid_only"] for r in separate_grid_only_load(raw))


def test_unrelated_grid_only_high_load_without_step_is_uncertain():
    raw = history()[6:18]
    clean = separate_grid_only_load(raw)
    assert not any(r["ev_grid_only"] for r in clean)


def test_training_history_cannot_use_samples_after_issue_time():
    raw = history()
    origin = datetime(2026, 10, 1, 0, 45, tzinfo=PRAGUE)
    first = household_training_history([(origin.date(), raw)], origin)
    poisoned = [dict(r, load=90000, cbat=1) if i >= 9 else r for i, r in enumerate(raw)]
    assert household_training_history([(origin.date(), poisoned)], origin) == first
    assert len(first[0][1]) == 9
