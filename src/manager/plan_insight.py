"""Short deterministic explanations from actual priced decisions; no LLM calls."""
from datetime import datetime, timedelta
from .domain import PRAGUE


def explain_plan(plan, prices):
    if not plan:
        return []
    groups = {}
    for row in plan.get('outlook_decisions', plan.get('decisions', [])):
        t = datetime.fromisoformat(row['timestamp']).astimezone(PRAGUE)
        groups.setdefault(str(t.date()), []).append((t, row))
    result = []
    for day, rows in sorted(groups.items()):
        bought = sum(max(0, r.get('grid_charge_kw', 0)) * .25 for _, r in rows)
        afternoon = [r['soc_percent'] for t, r in rows if 12 <= t.hour < 17]
        first = f'Planned grid charging adds {bought:.1f} kWh' if bought > .01 else 'No further grid charging is planned'
        if afternoon:
            first += f'; forecast afternoon charge peaks near {max(afternoon):.0f}%'
        evening = []
        for r in prices:
            t = datetime.fromisoformat(r['timestamp']).astimezone(PRAGUE)
            if str(t.date()) == day and 17 <= t.hour < 23 and r.get('import_eur_kwh') is not None:
                evening.append((t, r['import_eur_kwh']))
        second = None
        if evening:
            peak, cost = max(evening, key=lambda x: x[1])
            second = f'Evening import peaks at {peak:%H:%M} (€{cost * 1000:.0f}/MWh)'
            at_nine = [r for t, r in rows if (t + timedelta(minutes=15)).strftime('%H:%M') == '21:00']
            if at_nine:
                second += f'; projected charge at 21:00 is {at_nine[0]["soc_percent"]:.0f}%'
        result.append({'day': day, 'text': first + '.' + (' ' + second + '.' if second else ''),
                       'basis': 'Current forecast and import prices including variable fees; future actions may change.'})
    return result
