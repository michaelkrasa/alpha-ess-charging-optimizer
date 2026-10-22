"""NAS updates must preserve control intent and restore the old image on failure."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from ops.nas.update import update, wait_for_cycle


class Runner:
    def __init__(self, image='sha256:new'):
        self.calls = []
        self.image = image
        self.started = datetime.now(timezone.utc)

    def __call__(self, command, timeout=300):
        self.calls.append(command)
        if 'exec' in command:
            return json.dumps({'enabled': False, 'live': True})
        if '{{.Image}}' in command:
            return 'sha256:old\n'
        if '{{.Id}}' in command:
            return self.image
        if '{{.State.StartedAt}}' in command:
            return self.started.isoformat()
        return ''


def test_update_preserves_disabled_intent_and_uses_local_image_only(tmp_path):
    runner = Runner()
    checks = []
    def verify(since, enabled, live, runner):
        checks.append((since, enabled, live))
        return {'health': 'observing'}
    result = update(tmp_path, tmp_path/'image.gz', 'sha256:new', runner, verify)
    assert result['status'] == 'updated'
    assert checks == [(runner.started, False, True)]
    up = next(c for c in runner.calls if 'up' in c)
    assert up[-2:] == ['--pull', 'never'] and '--force-recreate' in up
    assert '--no-build' in up
    assert any(c[-2:] == ['sha256:old', 'alphaess-household:rollback'] for c in runner.calls)


def test_bad_image_identity_restores_old_tag(tmp_path):
    runner = Runner(image='sha256:unexpected')
    with pytest.raises(RuntimeError, match='previous image restored'):
        update(tmp_path, tmp_path/'image.gz', 'sha256:new', runner, lambda *a, **k: {})
    assert any(c[-2:] == ['sha256:old', 'alphaess-household:local'] for c in runner.calls)


def test_failed_new_controller_cycle_rolls_back_and_verifies_old_image(tmp_path):
    runner = Runner()
    checks = []
    def verify(*args, **kwargs):
        checks.append(args)
        if len(checks) == 1:
            raise RuntimeError('New image failed')
        return {}
    with pytest.raises(RuntimeError, match='previous image restored'):
        update(tmp_path, tmp_path/'image.gz', 'sha256:new', runner, verify)
    assert len(checks) == 2
    assert sum('up' in c for c in runner.calls) == 2


def test_old_cached_health_cannot_pass_post_restart_verification():
    now = datetime.now(timezone.utc)
    attempts = []
    def runner(command, timeout=300):
        attempts.append(command)
        return json.dumps({'enabled': True, 'live': True, 'planner': 'emhass', 'health': 'active',
                           'connected': 'connected', 'cycle': (now-timedelta(minutes=1)).isoformat(),
                           'contact': now.isoformat()})
    with pytest.raises(RuntimeError, match='fresh successful'):
        wait_for_cycle(now, True, True, runner, pause=lambda _: None, attempts=2)
    assert len(attempts) == 2


def test_export_uses_runtime_config_identity_across_docker_engines(tmp_path):
    import hashlib
    import io
    import tarfile
    from scripts.deploy_nas import exported_image_id
    config = b'{"architecture":"arm64","config":{"User":"manager"}}'
    manifest = [{'Config': 'attestation.json', 'RepoTags': []},
                {'Config': 'runtime.json', 'RepoTags': ['alphaess-household:local']}]
    archive = tmp_path/'image.tar.gz'
    with tarfile.open(archive, 'w:gz') as saved:
        for name, payload in [('manifest.json', json.dumps(manifest).encode()), ('runtime.json', config)]:
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            saved.addfile(member, io.BytesIO(payload))
    assert exported_image_id(archive) == 'sha256:' + hashlib.sha256(config).hexdigest()
