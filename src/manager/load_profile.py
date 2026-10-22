"""Expected household energy from completed, elapsed-time-integrated days."""

import math
import statistics
from datetime import datetime
from itertools import pairwise

from .domain import PRAGUE, day_slots

LOAD_PROFILE_VERSION = "mean-integrated-v2"


def completed_day_load(rows, day):
    if len(day_slots(day)) != 96:
        raise ValueError("Offsetless DST day is not usable for load calibration")
    points = []
    for row in rows or []:
        try:
            stamp = datetime.fromisoformat(
                str(row.get("uploadTime") or row.get("time"))
            )
            if stamp.tzinfo:
                stamp = stamp.astimezone(PRAGUE)
            if stamp.date() != day:
                continue
            kw = float(row["load"]) / 1000
            if kw < 0 or not math.isfinite(kw):
                continue
            points.append((stamp.hour * 3600 + stamp.minute * 60 + stamp.second, kw))
        except (ValueError, TypeError, KeyError):
            continue
    points.sort()
    if len(points) < 200 or points[0][0] > 600 or 86400 - points[-1][0] > 600:
        raise ValueError("Incomplete household load day")
    if any(b[0] - a[0] > 900 for a, b in pairwise(points)):
        raise ValueError("Household history has long gaps")
    points[0] = (0, points[0][1])
    energy = [0.0] * 96
    for (start, kw), (end, _) in zip(points, points[1:] + [(86400, points[-1][1])]):
        while start < end:
            i = start // 900
            stop = min(end, (i + 1) * 900)
            energy[i] += kw * (stop - start) / 3600
            start = stop
    return [e * 4 for e in energy]


def expected_profile(history, now):
    today = now.astimezone(PRAGUE).date()
    profiles = []
    for day, rows in history:
        if day >= today:
            continue
        try:
            profiles.append(completed_day_load(rows, day))
        except ValueError:
            continue
    if len(profiles) < 6:
        raise ValueError(
            "At least six complete past days are required for expected load"
        )
    return {str(i): statistics.mean(p[i] for p in profiles) for i in range(96)}


def charging_load_ceiling(history, now):
    profiles = []
    for day, rows in history:
        if day >= now.astimezone(PRAGUE).date():
            continue
        try:
            profiles.append(completed_day_load(rows, day))
        except ValueError:
            continue
    if len(profiles) < 6:
        raise ValueError("Insufficient completed history for charging headroom")
    return {
        str(i): max(p[(i + j) % 96] for p in profiles for j in range(-2, 3))
        for i in range(96)
    }
