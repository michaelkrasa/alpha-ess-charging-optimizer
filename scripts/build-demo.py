#!/usr/bin/env python3
"""Build the real dashboard as a portable, offline demo with synthetic data only."""
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def sample_state():
    start = datetime(2026, 6, 17, 22, tzinfo=timezone.utc)
    now = start + timedelta(hours=18, minutes=15)
    stamp = lambda t: t.isoformat()
    charge = {'gridCharge': True, 'timeChaf1': '01:00', 'timeChae1': '03:00',
              'timeChaf2': '13:00', 'timeChae2': '14:00', 'batHighCap': 100}
    discharge = {'ctrDis': True, 'timeDisf1': '06:00', 'timeDise1': '10:00',
                 'timeDisf2': '17:00', 'timeDise2': '23:45', 'batLowCap': 10}
    prices, decisions, measurements = [], [], []
    energy, capacity, reserve = 6.4, 14.2, 1.42
    current = None
    for i in range(192):
        t = start + timedelta(minutes=15 * i)
        hour = (i % 96) / 4
        spot = (48 if hour < 5 else 310 if 7 <= hour < 10 else
                -18 if 12 <= hour < 14 else 395 if 17 <= hour < 21 else 135)
        spot += 13 * math.sin(i * 0.61) + (9 if i >= 96 else 0)
        buy, sell = (spot / 1000 + 0.095) * 1.21, spot / 1000 - 0.01
        load = 0.65 + 0.18 * (1 + math.sin(i * 0.73))
        load += 0.75 if 6 <= hour < 10 else 1.5 if 17 <= hour < 22 else 0
        solar = max(0, 1.9 * math.sin((hour - 6) * math.pi / 13)) if 6 < hour < 19 else 0
        action = ('charge' if 1 <= hour < 3 or 13 <= hour < 14 else
                  'self_consume' if 6 <= hour < 10 or 17 <= hour < 23.75 else 'hold')
        native = max(0, solar - load)
        charged = min(5 if action == 'charge' else native, (capacity - energy) / .95 / .25)
        supplied = min(5, max(0, load - solar), max(0, energy - reserve) * .95 / .25) if action == 'self_consume' else 0
        energy += (charged * .95 - supplied / .95) * .25
        grid_charge = max(0, charged - native)
        row = {'timestamp': stamp(t), 'spot_eur_mwh': round(spot, 2),
               'import_eur_kwh': buy, 'export_eur_kwh': sell, 'tariff': 'NT',
               'tariff_verified': True, 'elapsed': t < now}
        prices.append(row)
        decisions.append({**row, 'action': action, 'grid_charge_kw': grid_charge,
                          'battery_load_kw': supplied, 'solar_charge_kw': min(native, charged),
                          'soc_percent': energy / capacity * 100, 'load_kw': load,
                          'solar_kw': solar, 'reason': {
                              'charge': 'Illustrative plan: store lower-price energy before the next demand peak.',
                              'self_consume': 'Illustrative plan: supply the house during the higher-price evening period.',
                              'hold': 'Illustrative plan: preserve stored energy; surplus solar can still charge the battery.'}[action]})
        measurement = {'timestamp': stamp(t), 'soc_percent': energy / capacity * 100,
                       'capacity_kwh': capacity, 'load_kw': load, 'solar_kw': solar,
                       'grid_kw': load - solar + charged - supplied,
                       'battery_kw': supplied - charged, 'inverter': 'Demo ESS'}
        if t <= now:
            measurements.append(measurement)
            current = measurement
    future = [d for d in decisions if d['timestamp'] > stamp(now)]
    schedule = {'charge': charge, 'discharge': discharge, 'checked_at': stamp(now),
                'source': 'pair_readback', 'readback_pending': False}
    actual = [{'timestamp': r['timestamp'], 'actual_soc': r['soc_percent']} for r in measurements]
    past_expected = [{'timestamp': r['timestamp'], 'expected_soc': max(0, min(100, r['actual_soc'] + 1.4 * math.sin(i / 7))),
                      'actual_soc': r['actual_soc'], 'delta_pp': r['actual_soc'] - max(0, min(100, r['actual_soc'] + 1.4 * math.sin(i / 7))),
                      'issued_at': stamp(datetime.fromisoformat(r['timestamp']) - timedelta(minutes=30))}
                     for i, r in enumerate(actual)]
    future_expected = [{'timestamp': stamp(datetime.fromisoformat(r['timestamp']) + timedelta(minutes=15)),
                        'expected_soc': r['soc_percent'], 'actual_soc': None, 'delta_pp': None, 'issued_at': stamp(now)} for r in future]
    return {'now': stamp(now), 'daily_energy': {'day': now.astimezone(ZoneInfo('Europe/Prague')).date().isoformat(), 'checked_at': stamp(now), 'solar_generated_kwh': sum(r['solar_kw'] * .25 for r in measurements), 'battery_charged_kwh': sum(max(0, -r['battery_kw']) * .25 for r in measurements)}, 'soc_tracking': {'actual': actual, 'rows': past_expected + future_expected,
            'latest': past_expected[-1], 'mean_absolute_error_pp': 1.0}, 'price_intervals': prices,
            'control': {'enabled': True, 'revision': 1, 'restore_pending': False},
            'health': {'status': 'active', 'timestamp': stamp(now), 'message': 'Sample controller cycle completed.'},
            'telemetry': current, 'device_connection': {'status': 'connected',
                'last_success_at': stamp(now), 'checked_at': stamp(now), 'transport': 'Simulated cloud API'},
            'energy_day': {'start': stamp(start), 'end': stamp(start + timedelta(days=1)),
                'rows': measurements, 'ticks': [{'timestamp': stamp(start + timedelta(hours=h)),
                    'label': '24:00' if h == 24 else f'{h:02}:00'} for h in (0, 6, 12, 18, 24)]},
            'plan': {'created_at': stamp(now), 'mode': 'legacy_daily', 'planner': 'emhass',
                'charge': charge, 'discharge': discharge, 'valid_until': stamp(start + timedelta(days=1)),
                'decisions': future, 'outlook_decisions': future,
                'notes': ['Synthetic illustration. This sample was not optimized for a real household.']},
            'device_schedule': schedule, 'applied_schedule': schedule,
            'actuation': {'status': 'accepted', 'schedule_verified': True, 'action': 'daily_schedule',
                'timestamp': stamp(now), 'message': 'Simulated settings confirmation; no inverter is connected.'},
            'standard_planner': 'emhass', 'planner_comparison': None, 'load_profile': {},
            'replay': None, 'readiness': [], 'live': True, 'reserve_percent': 10,
            'import_limit_kw': None, 'price_source': 'demo', 'control_mode': 'legacy_daily',
            'write_interval_seconds': 10, 'daily_write': None, 'history': []}


def stable_numbers(value):
    # libm may differ in the final floating-point bits between Linux and macOS.
    if isinstance(value, float):
        return round(value, 6) or 0.0
    if isinstance(value, dict):
        return {key: stable_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [stable_numbers(item) for item in value]
    return value


def build():
    source = (ROOT / 'src/manager/dashboard.html').read_text()
    sys.path.insert(0, str(ROOT))
    from src.manager.plan_insight import explain_plan
    data = sample_state()
    data['plan_insights'] = explain_plan(data['plan'], data['price_intervals'])
    state = json.dumps(stable_numbers(data), separators=(',', ':'), allow_nan=False)
    script = '''
const demoState = DEMO_STATE;
const demoSchedule = JSON.parse(JSON.stringify(demoState.device_schedule));
async function demoFetch(path, options={}) {
  if(path==='/api/control') {
    demoState.control.enabled=JSON.parse(options.body).enabled;
    demoState.health.status=demoState.control.enabled?'active':'observing';
    demoState.device_schedule=JSON.parse(JSON.stringify(demoSchedule));
    if(!demoState.control.enabled){
      demoState.device_schedule.charge.gridCharge=false;
      demoState.device_schedule.discharge.ctrDis=false;
    }
    demoState.applied_schedule=JSON.parse(JSON.stringify(demoState.device_schedule));
    demoState.actuation.action=demoState.control.enabled?'daily_schedule':'native';
  } else if(path!=='/api/state') throw Error('Demo has no external API');
  return {ok:true,json:async()=>JSON.parse(JSON.stringify(demoState))};
}
'''.replace('DEMO_STATE', state)
    source = source.replace('<head>', '<head><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src \'self\' data:; connect-src \'none\'; base-uri \'none\'; form-action \'none\'">')
    source = source.replace('<title>Household · Energy manager</title>', '<title>AlphaESS Energy Manager · Interactive demo</title>')
    banner = '''<aside class="demo-banner"><div><strong>Interactive demo</strong><span>Synthetic data · controls affect only this page · sample time 18:15</span></div><nav><a href="https://github.com/michaelkrasa/alpha-ess-charging-optimizer">GitHub ↗</a><a href="index.html" download="alphaess-demo.html">Download HTML ↓</a></nav></aside>'''
    css = '''.demo-banner{max-width:1240px;margin:18px auto 0;padding:14px 24px;background:#e8eddf;border:1px solid #d3ddc8;border-radius:10px;display:flex;justify-content:space-between;gap:16px;align-items:center;font-size:12px}.demo-banner strong{display:block;color:#41674b;font-size:14px}.demo-banner span{color:#697361}.demo-banner nav{display:flex;gap:18px;flex-shrink:0}.demo-banner a{color:#41674b;text-underline-offset:3px}@media(max-width:700px){.demo-banner{margin:12px;display:block}.demo-banner nav{margin-top:10px}}'''
    source = source.replace('</style>', css + '</style>').replace('<body><main>', '<body>' + banner + '<main>')
    source = source.replace('<script>', '<script>' + script).replace("fetch('/api/", "demoFetch('/api/")
    source = source.replace("'ESS connected'", "'Simulated ESS'").replace("'Connected · cloud API'", "'Simulated · cloud API'")
    return source


if __name__ == '__main__':
    target = ROOT / 'docs/index.html'
    content = build()
    if '--check' in sys.argv:
        if not target.exists() or target.read_text() != content:
            raise SystemExit('Demo is stale: run python scripts/build-demo.py')
    else:
        target.write_text(content)
        print(f'Built self-contained demo: {target}')
