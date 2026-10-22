"""Invoice-based marginal pricing; fixed standing charges are excluded.

Supplier native fees are converted once at ingestion using the CNB EUR rate.
Only EUR prices leave this module. Source: bezDodavatele, Spot/D56d, Jan 2026.
"""

from datetime import time

from .domain import PRAGUE


def schedule_verified(at, settings):
    day = at.astimezone(PRAGUE).date()
    return (
        settings.high_tariff_periods is not None
        and (settings.tariff_valid_from is None or day >= settings.tariff_valid_from)
        and (settings.tariff_valid_until is None or day < settings.tariff_valid_until)
    )


def high_tariff(at, periods):
    value = at.astimezone(PRAGUE).time().replace(tzinfo=None)
    for start, end in periods or []:
        a, b = time.fromisoformat(start), time.fromisoformat(end)
        if (a <= value < b) if a < b else (value >= a or value < b):
            return True
    return False


def prices(spot_eur_mwh, fx_per_eur, is_high):
    if not 10 < fx_per_eur < 100:
        raise ValueError("Invalid CNB EUR conversion rate")
    # Actual invoice: supplier + distribution + system services + electricity tax.
    distribution = 754.77 if is_high else 116.50
    import_adder_eur_mwh = (390 + distribution + 164.24 + 28.30) / fx_per_eur
    import_eur_kwh = (spot_eur_mwh + import_adder_eur_mwh) * 1.21 / 1000
    export_eur_kwh = (spot_eur_mwh - 390 / fx_per_eur) / 1000
    return import_eur_kwh, export_eur_kwh
