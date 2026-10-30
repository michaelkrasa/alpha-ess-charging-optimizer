import json

from src.manager.release import release_info


def test_only_matching_verified_deployment_is_shown(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_VERSION', 'abc123')
    database = tmp_path / 'manager.sqlite3'
    marker = tmp_path / 'deployment.json'
    assert release_info(database) == {'version': 'abc123', 'deployed_at': None}
    marker.write_text(json.dumps({'version': 'old', 'deployed_at': '2026-10-30T08:00:00+00:00'}))
    assert release_info(database)['deployed_at'] is None
    marker.write_text(json.dumps({'version': 'abc123', 'deployed_at': '2026-10-30T08:00:00+00:00'}))
    assert release_info(database)['deployed_at'] == '2026-10-30T08:00:00+00:00'
    marker.write_text('invalid')
    assert release_info(database)['deployed_at'] is None
