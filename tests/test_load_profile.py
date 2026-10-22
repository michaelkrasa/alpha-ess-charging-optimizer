from datetime import date, datetime, timedelta
import pytest
from src.manager.load_profile import expected_profile, completed_day_load
from src.manager.domain import PRAGUE


def test_intermittent_load_energy_is_not_erased_by_median():
    now = datetime(2026, 10, 20, 16, tzinfo=PRAGUE)
    history = []
    for i in range(1, 8):
        day = now.date() - timedelta(days=i)
        rows = [
            {
                "uploadTime": f"{day} {j // 12:02d}:{j % 12 * 5:02d}:00",
                "load": 7000 if j // 12 == i else 1000,
            }
            for j in range(288)
        ]
        history.append((day, rows))
    profile = expected_profile(history, now)
    assert sum(profile.values()) * 0.25 == pytest.approx(30)
    # Future actual data must not affect the causal expected-energy profile.
    history.append(
        (now.date(), [{"uploadTime": f"{now.date()} 00:00:00", "load": 1000000}] * 288)
    )
    assert expected_profile(history, now) == profile


def test_day_with_long_gaps_is_not_calibrated_as_lower_energy():
    day = date(2026, 10, 19)
    rows = [
        {"uploadTime": f"{day} {j // 12:02d}:{j % 12 * 5:02d}:00", "load": 1000}
        for j in range(288)
        if not 40 < j < 50
    ]
    with pytest.raises(ValueError, match="long gaps"):
        completed_day_load(rows, day)
