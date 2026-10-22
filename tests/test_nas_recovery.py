"""Recovery may edit only the approved package metadata, never spoof DSM."""

from datetime import datetime, timezone
import json

import pytest

from ops.nas.recover import RecoveryError, fingerprint, recover


@pytest.fixture
def deployment(tmp_path):
    system = tmp_path / "nas"
    app = tmp_path / "app"
    app.mkdir()
    files = {
        "etc.defaults/VERSION": 'productversion="7.4.1"\nbuildnumber="90080"\nsmallfixnumber="1"\n',
        "etc.defaults/synoinfo.conf": 'unique="synology_rtd1296_rs819"\n',
        "etc/synoinfo.conf": 'unique="synology_rtd1296_rs819"\n',
        "proc/sys/kernel/syno_hw_version": "RS819\n",
        "var/packages/ContainerManager/INFO": 'version="24.0.2-1706"\nexclude_model="synology_rtd1296_rs819"\nkeep="unchanged"\n',
    }
    for name, content in files.items():
        path = system / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return system, app


class FakeRunner:
    def __init__(self, ready=False, health="observing"):
        self.ready = ready
        self.calls = []
        self.health = health

    def run(self, command, timeout=30):
        self.calls.append(command)
        if command[-1] == "info":
            if not self.ready:
                raise RecoveryError("Docker unavailable")
            return "{}"
        if command[-2:] == ["start", "ContainerManager"]:
            self.ready = True
        if "exec" in command:
            return json.dumps(
                {
                    "planner": "emhass",
                    "live": False,
                    "enabled": False,
                    "health": self.health,
                    "connection": {
                        "status": "connected",
                        "last_success_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
            )
        return ""


def test_unknown_version_never_edits_or_starts_package(deployment):
    system, app = deployment
    info = system / "var/packages/ContainerManager/INFO"
    original = info.read_bytes()
    runner = FakeRunner()
    with pytest.raises(RecoveryError, match="untested"):
        recover(app, system, runner, "docker", pause=lambda _: None)
    assert info.read_bytes() == original
    assert len(runner.calls) == 1
    assert not (app / "recovery-backups").exists()


def test_approved_repair_backs_up_and_removes_only_exclusion(deployment):
    system, app = deployment
    current = fingerprint(system)
    (app / "approved-nas-versions.json").write_text(json.dumps([current]))
    info = system / "var/packages/ContainerManager/INFO"
    original = info.read_bytes()
    models = [
        (system / p).read_bytes()
        for p in ("etc/synoinfo.conf", "etc.defaults/synoinfo.conf")
    ]
    runner = FakeRunner()
    result = recover(app, system, runner, "docker", pause=lambda _: None)
    assert result["status"] == "healthy"
    assert "exclude_model" not in info.read_text()
    assert 'keep="unchanged"' in info.read_text()
    assert list((app / "recovery-backups").iterdir())[0].read_bytes() == original
    assert models == [
        (system / p).read_bytes()
        for p in ("etc/synoinfo.conf", "etc.defaults/synoinfo.conf")
    ]
    compose = next(c for c in runner.calls if "compose" in c)
    assert "--no-build" in compose
    assert "--pull" in compose and compose[-1] == "never"
    assert not any("compose.nas.live.yaml" in part for part in compose)


def test_working_unknown_version_does_not_need_invasive_repair(deployment):
    system, app = deployment
    runner = FakeRunner(ready=True)
    info = system / "var/packages/ContainerManager/INFO"
    original = info.read_bytes()
    result = recover(app, system, runner, "docker", pause=lambda _: None)
    assert result["status"] == "healthy" and not result["repair_approved"]
    assert info.read_bytes() == original
    assert not any("synopkg" in c[0] for c in runner.calls)


def test_enrollment_requires_successful_controller_and_cloud_contact(deployment):
    system, app = deployment
    with pytest.raises(RecoveryError, match="Cannot enroll"):
        recover(
            app,
            system,
            FakeRunner(ready=True, health="degraded"),
            "docker",
            enroll=True,
            pause=lambda _: None,
        )
    assert not (app / "approved-nas-versions.json").exists()
    recover(
        app, system, FakeRunner(ready=True), "docker", enroll=True, pause=lambda _: None
    )
    assert json.loads((app / "approved-nas-versions.json").read_text()) == [
        fingerprint(system)
    ]


def test_unrestored_model_configuration_stops_recovery(deployment):
    system, app = deployment
    path = system / "etc/synoinfo.conf"
    path.write_text('unique="synology_rtd1619b_ds423"\n')
    runner = FakeRunner(ready=True)
    with pytest.raises(RecoveryError, match="not restored"):
        recover(app, system, runner, "docker")
    assert not runner.calls
    assert path.read_text() == 'unique="synology_rtd1619b_ds423"\n'


def test_package_info_symlink_is_rejected(deployment):
    system, app = deployment
    info = system / "var/packages/ContainerManager/INFO"
    outside = app / "unrelated"
    outside.write_text('version="unexpected"\n')
    info.unlink()
    info.symlink_to(outside)
    with pytest.raises(RecoveryError, match="unexpected"):
        recover(app, system, FakeRunner(ready=True), "docker")
    assert outside.read_text() == 'version="unexpected"\n'


def test_enrollment_waits_for_first_successful_cycle(deployment):
    system, app = deployment

    class WarmingRunner(FakeRunner):
        def run(self, command, timeout=30):
            response = super().run(command, timeout)
            if "exec" in command:
                self.health = "observing"
            return response

    runner = WarmingRunner(ready=True, health="starting")
    result = recover(app, system, runner, "docker", enroll=True, pause=lambda _: None)
    assert result["enrolled"]
    assert sum("exec" in c for c in runner.calls) == 2


def test_incomplete_version_metadata_fails_closed(deployment):
    system, app = deployment
    (system / "etc.defaults/VERSION").write_text('productversion="7.4.1"\n')
    runner = FakeRunner(ready=True)
    with pytest.raises(RecoveryError, match="incomplete"):
        recover(app, system, runner, "docker")
    assert not runner.calls


def test_upstream_changed_script_requires_review_and_never_runs(deployment):
    from ops.nas.recover import check_upstream

    system, app = deployment
    urls = []

    def fetch(url):
        urls.append(url)
        return json.dumps([{"sha": "a" * 40}]).encode() if len(urls) % 2 else b"new script"

    result = check_upstream(app, system, fetch)
    assert result["status"] == "review_required" and result["notify"]
    assert result["executed"] is False
    assert '/'+('a'*40)+'/' in urls[-1]
    assert not check_upstream(app, system, fetch)["notify"]
    (system / "etc.defaults/VERSION").write_text('productversion="7.5"\n')
    assert check_upstream(app, system, fetch)["notify"]


def test_upstream_network_failure_is_not_reported_as_current(deployment):
    from ops.nas.recover import check_upstream

    system, app = deployment

    def offline(url):
        raise OSError("offline")

    result = check_upstream(app, system, offline)
    assert result["status"] == "check_failed" and result["notify"]
    assert result["executed"] is False
