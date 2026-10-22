"""The passwordless helper accepts image bytes, never remote code or commands."""
import hashlib
import io
import json

import pytest

from ops.nas.install_deploy import rule_for
from ops.nas.receive import MAX_IMAGE_BYTES, receive


def packet(payload=b'image', **changes):
    info = {'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
            'image_id': 'sha256:' + 'a' * 64}
    info.update(changes)
    return json.dumps(info).encode() + b'\n' + payload


def test_complete_image_is_received_with_exact_digest(tmp_path):
    target = tmp_path / 'image.gz'
    info = receive(io.BytesIO(packet()), target)
    assert target.read_bytes() == b'image'
    assert info['image_id'] == 'sha256:' + 'a' * 64


def test_harmless_probe_creates_no_upload_or_update(tmp_path):
    target = tmp_path / 'image.gz'
    assert receive(io.BytesIO(b'{"probe":true}\n'), target) == {'probe': True}
    assert not target.exists()


@pytest.mark.parametrize('changes', [
    {'size': 0}, {'size': True}, {'size': MAX_IMAGE_BYTES + 1},
    {'image_id': '$(touch /tmp/no)'}, {'sha256': '../code.py'},
])
def test_bad_metadata_is_rejected_before_writing(tmp_path, changes):
    target = tmp_path / 'image.gz'
    with pytest.raises(ValueError):
        receive(io.BytesIO(packet(**changes)), target)
    assert not target.exists()


@pytest.mark.parametrize('body', [packet(size=100), packet(sha256='0' * 64), b'x' * 4097])
def test_truncation_corruption_and_unbounded_headers_fail(tmp_path, body):
    with pytest.raises(ValueError):
        receive(io.BytesIO(body), tmp_path / 'image.gz')


def test_sudo_rule_allows_only_fixed_no_argument_helper():
    assert rule_for('deploy') == 'deploy ALL=(root) NOPASSWD: /usr/local/sbin/alphaess-update ""\n'
    for user in ['deploy ALL=(ALL)', 'root\nALL', '-bad', '../root']:
        with pytest.raises(ValueError):
            rule_for(user)


def test_installer_without_visudo_verifies_real_passwordless_path(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from ops.nas import install_deploy
    rule = tmp_path / 'alphaess-update'
    monkeypatch.setattr(install_deploy, 'RULE', rule)
    monkeypatch.setattr(install_deploy.os, 'chown', lambda *args: None)
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"protocol":1,"uid":0}')
    monkeypatch.setattr(install_deploy.subprocess, 'run', run)
    install_deploy.install_policy('deploy', '/usr/bin/sudo')
    assert rule.read_text() == rule_for('deploy')
    command, options = calls[0]
    assert command == ['/usr/bin/sudo', '-n', '-u', 'deploy', '/usr/bin/sudo', '-n', '-k', '/usr/local/sbin/alphaess-update']
    assert options['input'] == '{"probe":true}\n'
    assert not rule.with_suffix('.candidate').exists()


@pytest.mark.parametrize('previous', [None, b'# previous known-good rule\n'])
def test_installer_rolls_back_policy_when_passwordless_probe_fails(tmp_path, monkeypatch, previous):
    from types import SimpleNamespace
    from ops.nas import install_deploy
    rule = tmp_path / 'alphaess-update'
    if previous is not None:
        rule.write_bytes(previous)
    monkeypatch.setattr(install_deploy, 'RULE', rule)
    monkeypatch.setattr(install_deploy.os, 'chown', lambda *args: None)
    monkeypatch.setattr(install_deploy.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout=''))
    with pytest.raises(RuntimeError, match='probe failed'):
        install_deploy.install_policy('deploy', '/usr/bin/sudo')
    assert (rule.read_bytes() if rule.exists() else None) == previous
    assert not rule.with_suffix('.candidate').exists()
