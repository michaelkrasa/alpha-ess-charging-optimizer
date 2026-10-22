#!/usr/bin/env python3
"""One-time NAS commissioning, run with sudo after SHA-256 verification.

Installs a pinned official SPK through the reviewed community workaround,
loads the tested ARM64 image, and stages the manager with control OFF.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid


INSTALLER_SHA = "de015c4c168d9415ebcdcfd492a1449eeff71f9237a95ff76330772f648fb5ef"
INSTALLER_COMMIT = "2f0ffa47a84316be7fc6423c28b6a763797a24e2"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run(command, timeout=300, **kwargs):
    return subprocess.run(command, check=True, timeout=timeout, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("sudo is required")
    stage = args.stage.resolve(strict=True)
    manifest_file = stage / "manifest.json"
    if sha(manifest_file) != args.manifest_sha256:
        raise RuntimeError("Manifest checksum mismatch")
    manifest = json.loads(manifest_file.read_text())
    if Path("/proc/sys/kernel/syno_hw_version").read_text().strip() != "RS819":
        raise RuntimeError("Unexpected NAS model")
    get = "/usr/syno/bin/synogetkeyvalue"
    put = "/usr/syno/bin/synosetkeyvalue"

    def value(path, key):
        return subprocess.check_output([get, path, key], text=True).strip()

    actual = [
        value("/etc.defaults/VERSION", key)
        for key in ("productversion", "buildnumber", "smallfixnumber")
    ]
    if actual != ["7.4.1", "90080", "1"]:
        raise RuntimeError("Unreviewed DSM version")
    work = Path("/volume1/@alphaess-ops") / ("deploy-" + uuid.uuid4().hex)
    work.mkdir(mode=0o700)
    os.umask(0o077)
    for name, digest in manifest["files"].items():
        if Path(name).name != name:
            raise RuntimeError("Unsafe manifest filename")
        source = stage / name
        if sha(source) != digest:
            raise RuntimeError("Payload checksum mismatch: " + name)
        shutil.copyfile(source, work / name)
        (work / name).chmod(0o600)
        if sha(work / name) != digest:
            raise RuntimeError("Copied payload checksum mismatch")

    def status(phase, **details):
        result = {
            "phase": phase,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            **details,
        }
        temporary = stage / ("status-" + uuid.uuid4().hex)
        with temporary.open("x") as output:
            json.dump(result, output)
        temporary.chmod(0o644)
        os.replace(temporary, stage / "status.json")
        print(json.dumps(result), flush=True)

    info = Path("/var/packages/ContainerManager/INFO")
    if not info.exists():
        status("installing_container_manager")
        source = work / "installer.sh"
        if sha(source) != INSTALLER_SHA:
            raise RuntimeError("Unreviewed community installer")
        text = source.read_text()
        prompt = r"(?m)^\s*until read -r -t20 .*?; do :; done$"
        text, count = re.subn(
            prompt,
            "    echo Unexpected_interactive_step >&2\n    restore_unique\n    exit 20",
            text,
        )
        if count != 2:
            raise RuntimeError("Installer prompt format changed")
        line = 'package_install ContainerManager "$targetvol" "Container Manager"'
        if text.count(line) != 1:
            raise RuntimeError("Installer call format changed")
        text = text.replace(
            line,
            "/usr/syno/bin/synopkg install "
            + str(work / "container-manager.spk")
            + " || { restore_unique; exit 21; }",
        )
        bounded = work / "installer-local-spk.sh"
        bounded.write_text(text)
        original = {
            path: value(path, "unique")
            for path in ("/etc/synoinfo.conf", "/etc.defaults/synoinfo.conf")
        }
        if any(v != "synology_rtd1296_rs819" for v in original.values()):
            raise RuntimeError("Model identifier was not restored before installation")
        for path in original:
            shutil.copy2(path, work / (Path(path).parent.name + "-synoinfo.conf"))
        try:
            run(
                ["/bin/bash", str(bounded)],
                timeout=420,
                env={
                    **os.environ,
                    "target": "/volume1",
                    "targetvol": "/volume1",
                    "PATH": "/usr/syno/bin:/usr/syno/sbin:/usr/bin:/usr/sbin:/bin:/sbin",
                },
                stdin=subprocess.DEVNULL,
            )
        finally:
            for path, original_value in original.items():
                run([put, path, "unique", original_value], timeout=15)
    if not info.exists() or value(str(info), "version") != "24.0.2-1706":
        raise RuntimeError("Unexpected installed Container Manager version")
    for path in ("/etc/synoinfo.conf", "/etc.defaults/synoinfo.conf"):
        if value(path, "unique") != "synology_rtd1296_rs819":
            raise RuntimeError("Model restoration check failed")
    docker = "/var/packages/ContainerManager/target/usr/bin/docker"
    for attempt in range(36):
        result = subprocess.run(
            [docker, "--host", "unix:///var/run/docker.sock", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            break
        time.sleep(5)
    else:
        raise RuntimeError("Docker did not become ready")
    status("loading_tested_image")
    run([docker, "load", "-i", str(work / "image.tar.gz")], timeout=600)
    run([docker, "tag", "alphaess-household:nas-test", "alphaess-household:local"])
    smoke = """from datetime import datetime,timedelta,timezone
import json,time,resource
from src.manager.domain import Slot,Telemetry
from src.manager.emhass_mpc import EmhassPlanner
from src.manager.settings import Settings
t=datetime(2026,10,22,tzinfo=timezone.utc)
slots=[Slot((t+timedelta(minutes=15*i)).isoformat(),10,.01 if i<4 else .5,.05,2,0,'NT') for i in range(16)]
start=time.monotonic()
plan=EmhassPlanner(Settings(charge_kw=8.6,discharge_kw=8.6,effective_capacity_kwh=14.2)).plan(slots,Telemetry(t.isoformat(),10,14.2,2,0,0,0))
assert len(plan)==16 and any(p.grid_charge_kw>0 for p in plan[:4])
assert all(10-1e-5<=p.soc_percent<=100+1e-5 for p in plan)
print(json.dumps({'passed':True,'solver_seconds':time.monotonic()-start,'max_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
"""
    result = run(
        [
            docker,
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:size=128m",
            "--memory",
            "1g",
            "alphaess-household:local",
            "python",
            "-",
        ],
        input=smoke,
        capture_output=True,
        timeout=180,
    )
    benchmark = json.loads(result.stdout.strip().splitlines()[-1])
    status("nas_solver_passed", benchmark=benchmark)
    shared = Path("/volume1/docker")
    if not shared.is_dir():
        status("waiting_for_docker_shared_folder", benchmark=benchmark)
        for _ in range(120):
            if shared.is_dir():
                break
            time.sleep(5)
        else:
            raise RuntimeError(
                "Create a DSM shared folder named docker on volume1, then retry"
            )
    app = shared / "alphaess-household"
    if app.exists():
        raise RuntimeError(
            "Deployment directory already exists; no existing state overwritten"
        )
    app.mkdir(mode=0o700)
    with tarfile.open(work / "app.tar.gz", "r:gz") as archive:
        for member in archive.getmembers():
            if (
                Path(member.name).is_absolute()
                or ".." in Path(member.name).parts
                or not (member.isfile() or member.isdir())
            ):
                raise RuntimeError("Unsafe archive member")
        archive.extractall(app)
    for path in [app, *app.rglob("*")]:
        os.chown(path, 0, 0)
        path.chmod(0o755 if path.is_dir() else 0o644)
    app.chmod(0o700)
    (app / ".env").chmod(0o600)
    for path in [app / "data", *(app / "data").rglob("*")]:
        os.chown(path, 10001, 10001)
        path.chmod(0o700 if path.is_dir() else 0o600)
    (app / "nas-live.enabled").touch(mode=0o600)
    status("ready_to_start", benchmark=benchmark, app_dir=str(app))
    # The Mac is stopped only after this actual-NAS offline test has passed.
    for _ in range(120):
        if (stage / "allow-start").is_file():
            break
        time.sleep(5)
    else:
        raise RuntimeError("Timed out waiting for Mac controller handover")
    run(
        [
            "/usr/bin/python3",
            str(app / "ops/nas/recover.py"),
            "--app-dir",
            str(app),
            "--enroll",
            "--notify-user",
            "misa",
        ],
        timeout=600,
    )
    startup = Path("/usr/local/etc/rc.d/S99alphaess-recover.sh")
    if startup.exists():
        raise RuntimeError("Startup hook already exists; no overwrite attempted")
    startup.parent.mkdir(parents=True, exist_ok=True)
    startup.write_text("""#!/bin/sh
case "$1" in
 start)
  (sleep 60; /usr/bin/python3 /volume1/docker/alphaess-household/ops/nas/recover.py --app-dir /volume1/docker/alphaess-household --notify-user misa >>/volume1/docker/alphaess-household/boot-recovery.log 2>&1) &
  ;;
 stop) ;;
esac
""")
    startup.chmod(0o755)
    # Simulate recovery now; a NAS reboot is not needed or requested.
    run(
        [
            "/usr/bin/python3",
            str(app / "ops/nas/recover.py"),
            "--app-dir",
            str(app),
            "--notify-user",
            "misa",
        ],
        timeout=300,
    )
    status(
        "ready_for_control",
        benchmark=benchmark,
        app_dir=str(app),
        control_enabled=False,
        recovery="Root boot hook installed and recovery command tested; actual reboot not tested",
    )
    # Remove private staging credentials; installed config remains root-only.
    (stage / "app.tar.gz").unlink(missing_ok=True)
    (work / "app.tar.gz").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
