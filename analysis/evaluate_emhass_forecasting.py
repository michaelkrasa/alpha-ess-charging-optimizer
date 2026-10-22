"""Causal, offline EMHASS forecasting evaluation on EV-separated household data.

Run: .venv-emhass/bin/python analysis/evaluate_emhass_forecasting.py
No Home Assistant connection, external requests, or inverter commands.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import logging
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd
from emhass.battery_identification import BatteryIdentification
from emhass.forecast import Forecast
from emhass.machine_learning_forecaster import MLForecaster

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.manager.consumption import separate_grid_only_load
DATA = ROOT / 'data/emhass'
OUTPUT = ROOT / 'analysis/output'
LOG = logging.getLogger('emhass-forecast-evaluation')
SPEC = {
    'package_version': '0.18.6', 'lag_steps': 96, 'train_days': 90,
    'refit_days': 7, 'forecast_steps': 192, 'score_steps': 96,
    'models': ['RidgeRegression', 'GradientBoostingRegressor'],
    'development': ['2025-06-15', '2025-06-28'],
    'holdouts': {'2025_autumn': ['2025-10-01', '2025-12-31'],
                 '2026_recent': ['2026-09-08', '2026-10-19']},
    'scoring': 'Only observed non-EV-inferred target rows; midnight Europe/Prague daily origin',
    'selection': 'Lowest development MAE; frozen before held-out runs',
}


def load_history():
    raw = pd.read_csv(DATA / 'household_15min.csv')
    raw.index = pd.DatetimeIndex(pd.to_datetime(raw.pop('timestamp'), utc=True))
    raw = raw.sort_index()
    return raw.reindex(pd.date_range(raw.index.min(), raw.index.max(), freq='15min'))


def _site_only_gap_context(raw, asof, frame):
    """Replace potentially retrospective labels with observable site power.

    An unknown recording interval must not be filled from the EV-cleaned
    export. Only fully observed quarters are integrated; gaps remain NaN.
    """
    boundaries = pd.date_range(asof-pd.Timedelta(hours=11), asof, freq='15min')
    replacement = pd.Series(np.nan, index=boundaries[:-1], dtype=float)
    if len(frame):
        times = np.r_[frame.index.as_unit('ns').asi8/1e9, asof.value/1e9]
        powers = frame.site_load_w.to_numpy(dtype=float)
        observed = (np.diff(times) > 0) & (np.diff(times) <= 900) & np.isfinite(powers)
        for stamp in replacement.index:
            start = stamp.value/1e9
            overlap = np.maximum(0, np.minimum(times[1:], start+900)-np.maximum(times[:-1], start))
            overlap = np.where(observed, overlap, 0)
            if overlap.sum() >= 900-1e-4:
                replacement.loc[stamp] = float(np.sum(np.nan_to_num(powers)*overlap)/900)
    result = raw.copy()
    present = replacement.index.intersection(result.index)
    # Preserve the export's excluded recording days; do not manufacture new
    # training coverage merely because one piece of an excluded day exists.
    known = result.loc[present, 'household_load_w'].notna()
    result.loc[present, 'household_load_w'] = replacement.reindex(present).where(known)
    result.attrs['causal_ev_gap_fallback'] = True
    return result


def causal_ev_context(raw, asof):
    """Redo newest labels with only observations available at this origin.

    Completed-episode labels can otherwise see a session end after forecast
    origin. Sessions are at most10h, so repair11h plus30min start context.
    """
    if not hasattr(causal_ev_context, '_five'):
        five = pd.read_csv(DATA / 'household_5min.csv')
        five.index = pd.DatetimeIndex(pd.to_datetime(five.pop('timestamp'), utc=True))
        causal_ev_context._five = five.sort_index()
    five = causal_ev_context._five
    start = asof-pd.Timedelta(hours=11)
    frame = five.loc[(five.index >= start-pd.Timedelta(minutes=30)) & (five.index < asof)]
    if len(frame) < 3:
        return _site_only_gap_context(raw, asof, frame)
    canonical = [dict(uploadTime=t.isoformat(), load=r.site_load_w, ppv=r.pv_w,
                      gridCharge=max(r.grid_w, 0), feedIn=max(-r.grid_w, 0), cbat=r.soc_percent)
                 for t,r in frame.iterrows()]
    cleaned = separate_grid_only_load(canonical)
    if not cleaned:
        return _site_only_gap_context(raw, asof, frame)
    times = np.array([pd.Timestamp(r['uploadTime']).value/1e9 for r in cleaned]+[asof.value/1e9])
    powers = np.array([r['load'] for r in cleaned])
    if times[0] > start.value/1e9 or np.max(np.diff(times)) > 900:
        return _site_only_gap_context(raw, asof, frame)
    cumulative = np.r_[0., np.cumsum(powers*np.diff(times))]
    boundaries = pd.date_range(start, asof, freq='15min')
    energy = np.interp(boundaries.as_unit('ns').asi8/1e9, times, cumulative)
    values = np.diff(energy)/900
    if np.mean(powers) > 0 and np.sum(values) <= 0:
        raise ValueError('Causal history integration lost positive measured energy')
    result = raw.copy()
    present = boundaries[:-1].intersection(result.index)
    replacement = pd.Series(values,index=boundaries[:-1]).reindex(present)
    result.loc[present, 'household_load_w'] = replacement
    return result


def historical_window(raw, asof):
    """Use only strictly earlier values and do not bridge long recording gaps."""
    frame = raw.loc[(raw.index < asof) & (raw.index >= asof - pd.Timedelta(days=90))]
    present = frame.household_load_w.notna()
    missing = ~present
    runs = missing.groupby(present.cumsum()).transform('sum')
    long_gap = frame.index[missing & (runs > 8)]
    if len(long_gap):
        frame = frame.loc[frame.index > long_gap[-1]]
    if len(frame) < 14 * 96 or frame.index[-1] != asof - pd.Timedelta(minutes=15):
        raise ValueError('Fewer than 14 contiguous history days or no latest observation')
    # Missing buckets inside an otherwise continuous segment are forward-filled.
    # EV-inferred values are the preprocessing baseline estimates, not metered labels.
    values = frame.household_load_w.ffill().bfill().clip(lower=0)
    values.index = values.index.tz_convert('Europe/Prague')
    values.index.freq = '15min'
    return values.to_frame('household_load_w')


def baselines(raw, asof, index):
    mean = []
    weekly = []
    for target in index:
        # Clock profile uses observations from seven completed earlier UTC days.
        local = target.tz_convert('Europe/Prague')
        clock = local.hour * 4 + local.minute // 15
        offsets = [asof.normalize() - pd.DateOffset(days=d) + pd.Timedelta(minutes=15*clock)
                   for d in range(1, 8)]
        samples = raw.household_load_w.reindex(offsets).dropna()
        mean.append(float(samples.mean()) if len(samples) >= 5 else np.nan)
        previous = target - pd.DateOffset(days=7)
        weekly.append(float(raw.household_load_w.get(previous, np.nan)))
    # Latest-live mix is deliberately package-equivalent: first step only.
    latest_frame = raw.loc[raw.index < asof, ['household_load_w']].tail(1)
    mixed = Forecast.get_mix_forecast(
        latest_frame, pd.Series(mean, index=index), .5, .5, 'household_load_w').to_numpy()
    return {'seven_day_mean': np.array(mean), 'same_weekday': np.array(weekly),
            'mean_first_step_mix': mixed}


def metrics(records):
    result = {}
    if not records:
        return result
    frame = pd.concat(records, ignore_index=True)
    for model, group in frame.groupby('model'):
        err = group.prediction_w.to_numpy() - group.actual_w.to_numpy()
        actual = group.actual_w.to_numpy()
        peaks = actual >= np.quantile(actual, .9)
        result[model] = {
            'n': len(group), 'mae_w': float(np.mean(abs(err))),
            'rmse_w': float(np.sqrt(np.mean(err**2))), 'bias_w': float(np.mean(err)),
            'peak_mae_w': float(np.mean(abs(err[peaks]))),
            'peak_bias_w': float(np.mean(err[peaks])),
            'energy_actual_kwh': float(sum(actual)*.25/1000),
            'energy_prediction_kwh': float(sum(group.prediction_w)*.25/1000),
        }
    return result


def paired_daily_uncertainty(raw):
    """Paired seven-day block bootstrap; descriptive uncertainty, no selection."""
    rng = np.random.default_rng(4721)
    result = {}
    for name in SPEC['holdouts']:
        daily = []
        for path in sorted((DATA/'forecasts'/name).glob('*.csv')):
            frame = pd.read_csv(path)
            frame.index = pd.to_datetime(frame.timestamp, utc=True)
            frame = frame.iloc[:96]
            actual = raw.reindex(frame.index)
            valid = ((actual.ev_inferred == 0) & actual.household_load_w.notna() &
                     np.isfinite(frame.RidgeRegression) & np.isfinite(frame.seven_day_mean))
            if valid.any():
                errors = (abs(frame.loc[valid, 'RidgeRegression']-actual.loc[valid, 'household_load_w']) -
                          abs(frame.loc[valid, 'seven_day_mean']-actual.loc[valid, 'household_load_w']))
                daily.append((errors.sum(), len(errors)))
        daily = np.array(daily)
        samples = []
        for _ in range(2000):
            starts = rng.integers(0, len(daily), size=(len(daily)+6)//7)
            indices = np.concatenate([(start+np.arange(7)) % len(daily) for start in starts])[:len(daily)]
            resampled = daily[indices]
            samples.append(resampled[:, 0].sum()/resampled[:, 1].sum())
        result[name] = {'days': len(daily),
                        'ridge_minus_mean_mae_w': float(daily[:, 0].sum()/daily[:, 1].sum()),
                        'circular_7day_block_bootstrap_95pct': np.quantile(samples, [.025,.975]).tolist(),
                        'samples': 2000, 'seed': 4721}
    return result


def validate_and_manifest():
    destination = DATA/'forecasts'
    counts = {}
    for name in SPEC['holdouts']:
        count = missing = 0
        for path in sorted((destination/name).glob('*.csv')):
            frame = pd.read_csv(path)
            index = pd.to_datetime(frame.timestamp, utc=True)
            assert len(frame) == 192 and index.iloc[0] == pd.Timestamp(frame['asof'].iloc[0])
            assert (index.diff().dropna() == pd.Timedelta(minutes=15)).all()
            prediction = frame.RidgeRegression.to_numpy()
            assert not np.isinf(prediction).any()
            count += 1
            missing += int(np.isnan(prediction).all())
        counts[name] = {'profiles':count, 'all_nan_ridge_fallback_profiles':missing}
    intraday = destination/'intraday_2026-10-12_2026-10-18.npz'
    if intraday.exists():
        data = np.load(intraday)
        assert data['load_w'].shape == (672,192)
        assert np.isfinite(data['load_w']).all() and (data['load_w'] >= 0).all()
        counts['intraday'] = {'origins':672, 'leads':192,
                              'first_asof_utc':pd.Timestamp(data['asof_utc_ns'][0],tz='UTC').isoformat()}
    manifest = {
        'validated':counts,
        'package_versions':{k:importlib.metadata.version(k) for k in
                            ['emhass','pandas','numpy','scikit-learn','skforecast']},
        'input_sha256':{f.name:hashlib.sha256(f.read_bytes()).hexdigest()
                        for f in DATA.glob('household*.csv')},
        'causality':'Newest11h EV labels rebuilt from source samples strictly before each origin; no future targets supplied to forecasting.',
    }
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2))
    return manifest


async def evaluate_period(raw, name, dates, model_names):
    models = {}
    fit_at = {}
    results = []
    exceptions = []
    gap_fallbacks = []
    fits = []
    cache = DATA / 'forecasts' / name
    cache.mkdir(parents=True, exist_ok=True)
    for asof in pd.date_range(dates[0], dates[1], freq='1D', tz='Europe/Prague'):
        index = pd.date_range(asof, periods=192, freq='15min')
        past = causal_ev_context(raw, asof)
        if past.attrs.get('causal_ev_gap_fallback'):
            gap_fallbacks.append(asof.isoformat())
        predicted = baselines(past, asof, index)
        try:
            history = historical_window(past, asof)
            for model_name in model_names:
                if model_name not in models or asof - fit_at[model_name] >= pd.Timedelta(days=7):
                    started = time.perf_counter()
                    model = MLForecaster(history.copy(), 'household', 'household_load_w',
                                         model_name, 96, {'data_path': cache}, LOG)
                    await model.fit(split_date_delta='48h', perform_backtest=False)
                    models[model_name] = model
                    fit_at[model_name] = asof
                    fits.append({'model': model_name, 'asof': asof.isoformat(),
                                 'history_start': history.index[0].isoformat(),
                                 'training_end': model.data_train.index[-1].isoformat(),
                                 'seconds': time.perf_counter()-started})
                model = models[model_name]
                future = await model.generate_exog(history, 192, 'household_load_w')
                prediction = model.forecaster.predict(
                    steps=192, last_window=history.household_load_w,
                    exog=future.drop('household_load_w', axis=1),
                )
                predicted[model_name] = np.maximum(prediction.to_numpy().ravel(), 0)
        except Exception as error:
            exceptions.append({'asof': asof.isoformat(), 'error': repr(error)})
        for model_name in model_names:
            predicted.setdefault(model_name, np.full(192, np.nan))
        cached = pd.DataFrame(predicted, index=index)
        cached.index.name = 'timestamp'
        cached.insert(0, 'asof', asof.isoformat())
        cached.to_csv(cache / f'{asof:%Y-%m-%d}.csv')
        target = raw.reindex(index[:96])
        valid = target.household_load_w.notna() & (target.ev_inferred == 0)
        # Common target coverage across all requested methods.
        valid &= np.isfinite(cached.iloc[:96].drop(columns='asof')).all(axis=1)
        for model_name in predicted:
            row = pd.DataFrame({'model': model_name,
                                'actual_w': target.household_load_w[valid].to_numpy(),
                                'prediction_w': np.array(predicted[model_name])[:96][valid.to_numpy()]})
            results.append(row)
        if asof.day % 7 == 0:
            print(name, asof.date(), 'fits', len(fits), 'errors', len(exceptions), flush=True)
    return {'metrics': metrics(results), 'fits': fits, 'exceptions': exceptions,
            'causal_ev_gap_fallback_origins': gap_fallbacks,
            'asof_count': len(pd.date_range(dates[0], dates[1], freq='1D'))}


async def evaluate_intraday(raw, selected):
    """Frozen-model live last-window refresh; persist every causal 48h profile."""
    start = pd.Timestamp('2026-10-12', tz='Europe/Prague')
    stop = pd.Timestamp('2026-10-19', tz='Europe/Prague')
    history = historical_window(causal_ev_context(raw, start), start)
    model = MLForecaster(history.copy(), 'household', 'household_load_w', selected,
                         96, {'data_path': DATA / 'forecasts'}, LOG)
    await model.fit(split_date_delta='48h', perform_backtest=False)
    records = []
    origins, paths = [], []
    for asof in pd.date_range(start, stop, freq='15min', inclusive='left'):
        past = causal_ev_context(raw, asof)
        window = historical_window(past, asof)
        future = await model.generate_exog(window, 192, 'household_load_w')
        prediction = model.forecaster.predict(
            steps=192, last_window=window.household_load_w,
            exog=future.drop('household_load_w', axis=1))
        prediction = np.maximum(prediction.to_numpy().ravel(), 0)
        paths.append(prediction)
        origins.append(asof.tz_convert('UTC').value)
        actual = raw.loc[asof]
        daily_path = DATA / 'forecasts/2026_recent' / f'{asof:%Y-%m-%d}.csv'
        daily = pd.read_csv(daily_path)
        daily.index = pd.to_datetime(daily.timestamp, utc=True)
        previous = past.household_load_w.get(asof-pd.Timedelta(minutes=15), np.nan)
        forecasts = {
            'rolling_RidgeRegression': prediction[0],
            'midnight_RidgeRegression': daily.loc[asof, selected],
            'midnight_seven_day_mean': daily.loc[asof, 'seven_day_mean'],
            'last_measured': previous,
        }
        if actual.ev_inferred == 0 and np.isfinite(actual.household_load_w):
            for name, estimate in forecasts.items():
                if np.isfinite(estimate):
                    records.append(pd.DataFrame({'model': [name],
                                                 'actual_w': [actual.household_load_w],
                                                 'prediction_w': [estimate]}))
        if asof.hour == 0 and asof.minute == 0:
            print('intraday', asof.date(), flush=True)
    destination = DATA / 'forecasts/intraday_2026-10-12_2026-10-18.npz'
    np.savez_compressed(destination, asof_utc_ns=np.array(origins, dtype=np.int64),
                        load_w=np.array(paths), lead_minutes=np.arange(192)*15)
    return {'metrics': metrics(records), 'cache': str(destination.relative_to(ROOT)),
            'n_origins': len(origins), 'model': selected,
            'trained_before': model.data_train.index[-1].isoformat(),
            'method': 'One frozen model, refreshed actual past last_window every 15min; no future labels'}


def identify_battery():
    raw = pd.read_csv(DATA / 'household_5min.csv')
    raw.index = pd.DatetimeIndex(pd.to_datetime(raw.pop('timestamp'), utc=True))
    outputs = {}
    for name, start, end in [('2025', '2025-01-01', '2026-01-01'),
                             ('2026', '2026-06-01', '2026-10-20')]:
        frame = raw.loc[(raw.index >= pd.Timestamp(start, tz='UTC')) &
                        (raw.index < pd.Timestamp(end, tz='UTC'))]
        identified = BatteryIdentification(LOG, pd.Timedelta(minutes=5)).identify(
            frame, power_col='battery_ac_w', soc_col='soc_percent', configured_capacity_wh=17200)
        outputs[name] = identified.to_dict()
    return outputs


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--development-only', action='store_true')
    parser.add_argument('--holdouts-only', action='store_true')
    parser.add_argument('--intraday-only', action='store_true')
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    raw = load_history()
    frozen_path = OUTPUT / 'emhass_forecasting_frozen_spec.json'
    spec_hash = hashlib.sha256(json.dumps(SPEC, sort_keys=True).encode()).hexdigest()
    if not args.holdouts_only and not args.intraday_only:
        frozen_path.write_text(json.dumps({'spec': SPEC, 'spec_sha256': spec_hash,
                                         'selected_model': None}, indent=2))
        development = await evaluate_period(raw, 'development', SPEC['development'], SPEC['models'])
        (OUTPUT / 'emhass_forecasting_development.json').write_text(json.dumps(development, indent=2))
        selected = min(SPEC['models'], key=lambda m: development['metrics'][m]['mae_w'])
        frozen_path.write_text(json.dumps({'spec': SPEC, 'spec_sha256': spec_hash,
                                         'selected_model': selected,
                                         'selection_development_mae_w': development['metrics'][selected]['mae_w']}, indent=2))
        print('FROZEN', selected, flush=True)
    frozen = json.loads(frozen_path.read_text())
    if frozen['spec_sha256'] != spec_hash:
        raise ValueError('Specification changed after freeze')
    if args.intraday_only:
        intraday = await evaluate_intraday(raw, frozen['selected_model'])
        (OUTPUT / 'emhass_forecasting_intraday.json').write_text(json.dumps(intraday, indent=2))
        validate_and_manifest()
        print(json.dumps(intraday, indent=2), flush=True)
        return
    if not args.development_only:
        results = {}
        for name, dates in SPEC['holdouts'].items():
            results[name] = await evaluate_period(raw, name, dates, [frozen['selected_model']])
            (OUTPUT / f'emhass_forecasting_{name}.json').write_text(json.dumps(results[name], indent=2))
        battery = identify_battery()
        (OUTPUT / 'emhass_battery_identification.json').write_text(json.dumps(battery, indent=2))
        uncertainty = paired_daily_uncertainty(raw)
        (OUTPUT / 'emhass_forecast_paired_uncertainty.json').write_text(json.dumps(uncertainty, indent=2))
        validate_and_manifest()
        print(json.dumps({'holdout_metrics': {k:v['metrics'] for k,v in results.items()},
                          'battery_identification': battery}, indent=2), flush=True)


if __name__ == '__main__':
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(main())
