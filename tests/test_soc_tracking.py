from datetime import datetime, timedelta
from src.manager.domain import PRAGUE, UTC
from src.manager.soc_tracking import capture_forecast, dashboard_soc
from src.manager.store import SQLiteStore


def test_expectations_freeze_before_the_interval_and_do_not_rewrite_history(tmp_path):
    store = SQLiteStore(tmp_path/'soc.db')
    now = datetime(2026,10,22,12,0,tzinfo=PRAGUE)
    start = now + timedelta(minutes=15)
    def plan(soc):return {'decisions':[{'timestamp':start.isoformat(),'soc_percent':soc}]}
    capture_forecast(store,plan(50),now)
    capture_forecast(store,plan(55),now+timedelta(minutes=5))
    capture_forecast(store,plan(90),start+timedelta(minutes=1))
    point=next(iter(store.get('soc_tracking')['days']['2026-10-22'].values()))
    assert point['expected_soc']==55
    assert datetime.fromisoformat(point['timestamp'])==start+timedelta(minutes=15)
    assert datetime.fromisoformat(point['issued_at'])<start


def test_quarter_alignment_delta_missing_data_and_midnight_rotation(tmp_path):
    store=SQLiteStore(tmp_path/'soc.db');now=datetime(2026,10,22,12,tzinfo=PRAGUE)
    start=now+timedelta(minutes=15);target=start+timedelta(minutes=15)
    capture_forecast(store,{'decisions':[{'timestamp':start.isoformat(),'soc_percent':50}]},now)
    rows=[{'timestamp':(target+timedelta(minutes=m)).astimezone(UTC).isoformat(),'soc_percent':v} for m,v in [(-2,52),(2,54)]]
    result=dashboard_soc(store,target+timedelta(minutes=5),{'rows':rows})
    assert result['latest']['actual_soc']==53
    assert result['latest']['delta_pp']==3
    assert result['mean_absolute_error_pp']==3
    assert dashboard_soc(store,target,{'rows':rows[:1]})['latest'] is None
    gap=[{**rows[0],'timestamp':(target-timedelta(minutes=20)).isoformat()},rows[1]]
    assert dashboard_soc(store,target+timedelta(minutes=5),{'rows':gap})['latest'] is None
    capture_forecast(store,{'decisions':[]},now+timedelta(days=1))
    assert not store.get('soc_tracking')['days']


def test_history_cbat_soc_is_preserved_and_invalid_soc_is_omitted(tmp_path):
    from src.manager.energy_day import cache_power_history,dashboard_energy
    store=SQLiteStore(tmp_path/'day.db');now=datetime(2026,10,22,12,tzinfo=PRAGUE)
    cache_power_history(store,[{'uploadTime':'2026-10-22 11:58:00','load':1000,'ppv':2000,'cbat':63.2}],now)
    energy=dashboard_energy(store,now,[])
    assert energy['rows'][0]['soc_percent']==63.2
    assert store.get('energy_day')['soc_history_backfilled']
