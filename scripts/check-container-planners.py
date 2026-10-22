"""Offline packaging check of the legacy comparison's complete import path."""
from datetime import date
import logging

logging.basicConfig()  # The running web server already has logging handlers.
from src.manager.domain import PRAGUE, Slot, Telemetry, day_slots
from src.manager.legacy_policy import legacy_periods
from src.manager.settings import Settings

times = day_slots(date(2026, 6, 18))
slots = []
for t in times:
    hour = t.astimezone(PRAGUE).hour
    spot = 40 if hour < 6 else 180 if 17 <= hour < 22 else 80
    slots.append(Slot(t.isoformat(), spot, spot / 1000 + .1, .03, 1, 0, 'NT'))
telemetry = Telemetry(times[0].isoformat(), 50, 14, 1, 0, 0, 0)
charge, discharge = legacy_periods(slots, telemetry, Settings())
assert isinstance(charge, tuple)
assert discharge is None or isinstance(discharge, tuple)
print('Legacy comparison runs in the packaged container without credentials/network')
