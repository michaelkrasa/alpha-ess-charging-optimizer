"""Offline released-EMHASS PV regression versus causal existing GTI calibration.

Run with .venv-emhass/bin/python analysis/evaluate_emhass_pv.py.
Uses archived IFS runs available with a six-hour publication lag. No network I/O.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from emhass.forecast import Forecast

from evaluate_emhass_forecasting import DATA, OUTPUT, ROOT, load_history, metrics

LOG = logging.getLogger('emhass-pv-evaluation')
SPEC = {
    'package': 'emhass==0.18.6', 'regression_model': 'LassoRegression',
    'training_days': 90, 'refit_days': 7, 'n_splits': 5,
    'forecast_steps': 192, 'weather_publication_lag_hours': 6,
    'baseline': 'Previous completed-day median measured PV / issued GTI, .8 multiplier; max7day fallback',
    'periods': {'2025_autumn': ['2025-10-01', '2025-12-31'],
                '2026_recent': ['2026-09-08', '2026-10-19']},
    'configuration': 'Predeclared package-default Lasso; no holdout model selection',
}


class IssuedWeather:
    def __init__(self):
        self.cache = {}

    def forecast(self, asof, index):
        prior = asof.tz_convert('UTC') - pd.Timedelta(hours=6)
        run = prior.floor('6h')
        name = run.strftime('%Y-%m-%dT%H')
        if name not in self.cache:
            path = ROOT / 'data/backtests/weather' / f'{name}.json'
            if not path.exists():
                raise ValueError('Archived issued weather unavailable: '+name)
            payload = json.loads(path.read_text())['hourly']
            self.cache[name] = pd.Series(
                payload['global_tilted_irradiance'],
                index=pd.to_datetime(payload['time'], utc=True), dtype=float)
        # Archived GTI is preceding-hour average labeled at its end.
        targets = index.tz_convert('UTC').floor('1h') + pd.Timedelta(hours=1)
        values = self.cache[name].reindex(targets).to_numpy()
        if not np.isfinite(values).all():
            raise ValueError('Archived forecast horizon incomplete: '+name)
        return np.maximum(values, 0)


def make_forecast():
    cfg = yaml.safe_load((ROOT / 'solar_config.yaml').read_text())
    retrieve = {'optimization_time_step': pd.Timedelta(minutes=15),
                'time_zone': 'Europe/Prague', 'method_ts_round': 'first',
                'sensor_power_photovoltaics': 'pv_w',
                'sensor_power_photovoltaics_forecast': 'baseline_pv_w',
                'sensor_power_load_no_var_loads': 'household_load_w',
                'Latitude': cfg['latitude'], 'Longitude': cfg['longitude']}
    optim = {'delta_forecast_daily': pd.Timedelta(days=1),
             'adjusted_pv_solar_elevation_threshold': 10}
    return Forecast(retrieve, optim, {}, {'passed_data': {}},
                    {'data_path': DATA / 'forecasts/pv'}, LOG)


def historical_issued_table(raw, weather):
    rows = []
    start = raw.index.min().tz_convert('Europe/Prague').normalize()
    stop = raw.index.max().tz_convert('Europe/Prague').normalize()
    for asof in pd.date_range(start, stop, freq='1D'):
        index = pd.date_range(asof, asof+pd.DateOffset(days=1), freq='15min', inclusive='left')
        try:
            gti = weather.forecast(asof, index)
        except ValueError:
            continue
        actual = raw.pv_w.reindex(index)
        rows.append(pd.DataFrame({'pv_w': actual.to_numpy(), 'gti': gti}, index=index))
    return pd.concat(rows).sort_index()


def scale_at(history, asof):
    for age in range(8):
        end = asof.normalize() - pd.DateOffset(days=age)
        start = end - pd.DateOffset(days=1)
        previous = history.loc[(history.index >= start) & (history.index < end)]
        usable = previous.loc[(previous.gti >= 200) & (previous.pv_w > 0), ['pv_w', 'gti']].dropna()
        if len(usable) >= 6:
            return float(np.median(usable.pv_w / usable.gti)) * .8
    raise ValueError('No causal calibration within7days')


async def main():
    OUTPUT.mkdir(exist_ok=True)
    freeze = {'spec': SPEC, 'sha256': hashlib.sha256(json.dumps(SPEC, sort_keys=True).encode()).hexdigest()}
    (OUTPUT / 'emhass_pv_frozen_spec.json').write_text(json.dumps(freeze, indent=2))
    raw, weather = load_history(), IssuedWeather()
    historical = historical_issued_table(raw, weather)
    # Every historical baseline is computed from strictly earlier completed days.
    historical['baseline_pv_w'] = np.nan
    for asof in pd.date_range(historical.index.min().normalize(), historical.index.max().normalize(), freq='1D'):
        try:
            scale = scale_at(historical, asof)
        except ValueError:
            continue
        mask = (historical.index >= asof) & (historical.index < asof+pd.DateOffset(days=1))
        historical.loc[mask, 'baseline_pv_w'] = historical.loc[mask, 'gti'] * scale
    results = {}
    for period, dates in SPEC['periods'].items():
        records, errors, fits = [], [], []
        model, trained = None, None
        destination = DATA / 'forecasts/pv' / period
        destination.mkdir(parents=True, exist_ok=True)
        for asof in pd.date_range(*dates, freq='1D', tz='Europe/Prague'):
            try:
                index = pd.date_range(asof, periods=192, freq='15min')
                baseline = weather.forecast(asof, index) * scale_at(historical, asof)
                if trained is None or asof-trained >= pd.Timedelta(days=7):
                    train = historical.loc[(historical.index < asof) &
                                           (historical.index >= asof-pd.Timedelta(days=90)),
                                           ['pv_w', 'baseline_pv_w']].dropna()
                    if len(train) < 14*96:
                        raise ValueError('Not enough pre-origin training history')
                    model = make_forecast()
                    model.adjust_pv_forecast_data_prep(train)
                    await model.adjust_pv_forecast_fit(n_splits=5, regression_model='LassoRegression', debug=True)
                    trained = asof
                    fits.append({'asof': asof.isoformat(), 'training_end': train.index[-1].isoformat(), 'n':len(train)})
                corrected = model.adjust_pv_forecast_predict(pd.DataFrame({'forecast':baseline}, index=index))
                adjusted = corrected.adjusted_forecast.to_numpy()
                cached = pd.DataFrame({'asof': asof.isoformat(), 'baseline_pv_w': baseline,
                                       'emhass_adjusted_pv_w': adjusted}, index=index)
                cached.index.name = 'timestamp'
                cached.to_csv(destination / f'{asof:%Y-%m-%d}.csv')
                actual = raw.pv_w.reindex(index[:96])
                valid = actual.notna().to_numpy()
                for name, prediction in [('baseline_pv', baseline), ('emhass_adjusted_pv', adjusted)]:
                    records.append(pd.DataFrame({'model':name, 'actual_w':actual.to_numpy()[valid],
                                                 'prediction_w':prediction[:96][valid]}))
            except Exception as exc:
                errors.append({'asof':asof.isoformat(), 'error':repr(exc)})
            if asof.day%7==0:
                print(period,asof.date(),'fits',len(fits),'errors',len(errors),flush=True)
        results[period]={'metrics':metrics(records),'fits':fits,'exceptions':errors}
        (OUTPUT/f'emhass_pv_{period}.json').write_text(json.dumps(results[period],indent=2))
    print(json.dumps({k:v['metrics'] for k,v in results.items()},indent=2),flush=True)


if __name__ == '__main__':
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(main())
