"""Recording gaps must never restore retrospectively inferred EV labels."""

import numpy as np
import pandas as pd
import pytest

pytest.importorskip('emhass')

from analysis.evaluate_emhass_forecasting import causal_ev_context


def history(asof):
    index = pd.date_range(asof-pd.Timedelta(hours=12), asof+pd.Timedelta(hours=1), freq='15min')
    return pd.DataFrame({'household_load_w':100., 'pv_w':0., 'ev_inferred':1}, index=index)


def meter(index):
    return pd.DataFrame({'site_load_w':800., 'household_load_w':100., 'pv_w':0.,
                         'grid_w':800., 'battery_ac_w':0., 'soc_percent':50., 'ev_inferred':1}, index=index)


def test_gap_replaces_recent_inferred_labels_with_observed_site_power(monkeypatch):
    asof = pd.Timestamp('2026-10-12', tz='UTC')
    raw = history(asof)
    index = pd.date_range(asof-pd.Timedelta(hours=12), asof, freq='5min', inclusive='left')
    index = index[(index < asof-pd.Timedelta(hours=5)) | (index >= asof-pd.Timedelta(hours=4, minutes=30))]
    monkeypatch.setattr(causal_ev_context, '_five', meter(index), raising=False)
    repaired = causal_ev_context(raw, asof)
    assert repaired.attrs['causal_ev_gap_fallback']
    assert repaired.loc[asof-pd.Timedelta(minutes=15), 'household_load_w'] == 800
    assert np.isnan(repaired.loc[asof-pd.Timedelta(hours=5), 'household_load_w'])
    assert repaired.loc[asof-pd.Timedelta(hours=12), 'household_load_w'] == 100


def test_too_few_samples_do_not_restore_inferred_labels(monkeypatch):
    asof = pd.Timestamp('2026-10-12', tz='UTC')
    raw = history(asof)
    monkeypatch.setattr(causal_ev_context, '_five', meter(pd.DatetimeIndex([asof-pd.Timedelta(minutes=15)])), raising=False)
    repaired = causal_ev_context(raw, asof)
    assert repaired.attrs['causal_ev_gap_fallback']
    assert repaired.loc[asof-pd.Timedelta(minutes=15), 'household_load_w'] == 800
    assert np.isnan(repaired.loc[asof-pd.Timedelta(hours=1), 'household_load_w'])


def test_short_contiguous_tail_does_not_invent_zero_observations(monkeypatch):
    asof = pd.Timestamp('2026-10-12', tz='UTC')
    raw = history(asof)
    index = pd.date_range(asof-pd.Timedelta(minutes=20), asof, freq='5min', inclusive='left')
    monkeypatch.setattr(causal_ev_context, '_five', meter(index), raising=False)
    repaired = causal_ev_context(raw, asof)
    assert repaired.attrs['causal_ev_gap_fallback']
    assert repaired.loc[asof-pd.Timedelta(minutes=15), 'household_load_w'] == 800
    assert np.isnan(repaired.loc[asof-pd.Timedelta(hours=1), 'household_load_w'])


def test_future_meter_values_cannot_change_gap_repair(monkeypatch):
    asof = pd.Timestamp('2026-10-12', tz='UTC')
    raw = history(asof)
    index = pd.date_range(asof-pd.Timedelta(hours=12), asof+pd.Timedelta(hours=1), freq='5min')
    index = index[(index < asof-pd.Timedelta(hours=5)) | (index >= asof-pd.Timedelta(hours=4, minutes=30))]
    five = meter(index)
    monkeypatch.setattr(causal_ev_context, '_five', five, raising=False)
    first = causal_ev_context(raw, asof)
    five.loc[five.index >= asof, ['site_load_w','soc_percent']] = [100000.,0.]
    second = causal_ev_context(raw, asof)
    pd.testing.assert_frame_equal(first, second)
