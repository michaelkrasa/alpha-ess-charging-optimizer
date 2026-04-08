from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.optimizer import parse_target_date_arg


def test_parse_target_date_arg_accepts_day_of_month():
    timezone = ZoneInfo("Europe/Prague")
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone)

    parsed = parse_target_date_arg("15", timezone, now=now)

    assert parsed == datetime(2026, 4, 15, tzinfo=timezone)


def test_parse_target_date_arg_accepts_iso_date():
    timezone = ZoneInfo("Europe/Prague")
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone)

    parsed = parse_target_date_arg("2026-01-03", timezone, now=now)

    assert parsed == datetime(2026, 1, 3, tzinfo=timezone)


def test_parse_target_date_arg_rejects_invalid_value():
    timezone = ZoneInfo("Europe/Prague")

    with pytest.raises(ValueError):
        parse_target_date_arg("2026/01/03", timezone)
