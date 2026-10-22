"""Issued-ahead quarter-hour SOC expectations and timestamp-aligned measurements."""
from datetime import datetime, timedelta
import math

from .domain import PRAGUE, UTC, day_slots


def capture_forecast(store, plan, now):
    """Update future expectations only; retain each quarter once it starts."""
    today = now.astimezone(PRAGUE).date()
    days = (store.get('soc_tracking') or {}).get('days', {})
    days = {k: v for k, v in days.items() if str(today) <= k <= str(today + timedelta(days=1))}
    for row in plan.get('outlook_decisions', plan.get('decisions', [])):
        start = datetime.fromisoformat(row['timestamp'])
        value = row.get('soc_percent')
        if start.tzinfo is None or start <= now or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
            continue
        day = str(start.astimezone(PRAGUE).date())
        if not str(today) <= day <= str(today + timedelta(days=1)):
            continue
        end = (start + timedelta(minutes=15)).astimezone(UTC).isoformat()
        days.setdefault(day, {})[end] = {'timestamp': end, 'expected_soc': value, 'issued_at': now.isoformat()}
    store.put('soc_tracking', {'days': days})


def measured_at(points, target):
    """Interpolate only bracketed samples no more than ten minutes apart."""
    for i, (time, soc) in enumerate(points):
        if time == target:
            return soc
        if time > target:
            if not i:
                return None
            previous, value = points[i - 1]
            gap = (time - previous).total_seconds()
            if gap <= 0 or gap > 600:
                return None
            return value + (soc - value) * (target - previous).total_seconds() / gap
    return None


def dashboard_soc(store, now, energy):
    today = now.astimezone(PRAGUE).date()
    cached = (store.get('soc_tracking') or {}).get('days', {})
    expected = {p['timestamp']: p for day, rows in cached.items()
                if str(today) <= day <= str(today + timedelta(days=1)) for p in rows.values()}
    points = []
    for row in energy['rows']:
        soc = row.get('soc_percent')
        if isinstance(soc, (int, float)) and math.isfinite(soc) and 0 <= soc <= 100:
            points.append((datetime.fromisoformat(row['timestamp']), soc))
    points.sort()
    boundaries = [*day_slots(today), day_slots(today + timedelta(days=1))[0]]
    actual = []
    for t in boundaries:
        if t > now:
            continue
        value = measured_at(points, t)
        if value is not None:
            actual.append({'timestamp': t.astimezone(UTC).isoformat(), 'actual_soc': value})
    actual_map = {r['timestamp']: r['actual_soc'] for r in actual}
    rows = []
    for stamp, row in sorted(expected.items()):
        value = actual_map.get(stamp)
        rows.append({**row, 'actual_soc': value,
                     'delta_pp': None if value is None else value - row['expected_soc']})
    matched = [r for r in rows if r['delta_pp'] is not None]
    latest = matched[-1] if matched else None
    return {'rows': rows, 'actual': actual, 'latest': latest,
            'mean_absolute_error_pp': sum(abs(r['delta_pp']) for r in matched) / len(matched) if matched else None,
            'note': 'Past expectations were saved before the interval began. Measured SOC is aligned to quarter-hour boundaries; gaps are left blank.'}
