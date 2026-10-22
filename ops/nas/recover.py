#!/usr/bin/env python3
"""DSM boot recovery: repair approved metadata only; never run the installer.

Uses the NAS's Python standard library, independently of Docker/EMHASS.
Enroll a DSM/package pair only after commissioning it on the actual NAS.
"""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request


class RecoveryError(RuntimeError):
    pass


def fields(path):
    values = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"([A-Za-z_][A-Za-z_0-9]*)=(.*)", line.strip())
        if match:
            values[match[1]] = match[2].strip().strip('"')
    return values


def fingerprint(system):
    version = fields(system / "etc.defaults/VERSION")
    package = system / "var/packages/ContainerManager/INFO"
    if not package.is_file() or package.is_symlink():
        raise RecoveryError(
            "Container Manager is absent or its INFO path is unexpected; initial installation needs an administrator."
        )
    model = (system / "proc/sys/kernel/syno_hw_version").read_text().strip()
    if model != "RS819":
        raise RecoveryError("This deployment has only been prepared for RS819.")
    for relative in ("etc/synoinfo.conf", "etc.defaults/synoinfo.conf"):
        if fields(system / relative).get("unique") != "synology_rtd1296_rs819":
            raise RecoveryError(
                "NAS model identifier is not restored; administrator review required."
            )
    package_fields = fields(package)
    if not version.get("productversion") or not version.get("buildnumber") or not package_fields.get("version"):
        raise RecoveryError("DSM/package version metadata is incomplete; administrator review required.")
    return {
        "model": model,
        "dsm": "{}-{}-{}".format(
            version["productversion"],
            version["buildnumber"],
            version.get("smallfixnumber", "0"),
        ),
        "package": package_fields["version"],
    }


class Runner:
    def run(self, command, timeout=30):
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
        # Do not copy command output into status: third-party tools may log secrets.
        if result.returncode:
            raise RecoveryError(
                "Command failed: {} (exit {}).".format(
                    Path(command[0]).name, result.returncode
                )
            )
        return result.stdout


def docker_ready(runner, docker):
    try:
        runner.run(
            [docker, "--host", "unix:///var/run/docker.sock", "info"], timeout=15
        )
        return True
    except (RecoveryError, subprocess.TimeoutExpired):
        return False


def repair_metadata(system, app, current, approved):
    if current not in approved:
        raise RecoveryError(
            "Docker needs repair on an untested DSM/package combination; no system files changed."
        )
    info = system / "var/packages/ContainerManager/INFO"
    original = info.read_bytes()
    repaired = re.sub(
        rb"^exclude_model=[^\r\n]*(?:\r?\n|$)", b"", original, flags=re.MULTILINE
    )
    if original == repaired:
        return
    backup_dir = app / "recovery-backups"
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    backup = backup_dir / ("INFO-" + str(time.time_ns()))
    shutil.copy2(info, backup)
    # Replace atomically on the package filesystem, preserving permissions/owner.
    temporary = info.with_name("INFO.alphaess-new")
    try:
        with temporary.open("xb") as output:
            output.write(repaired)
            output.flush()
            os.fsync(output.fileno())
        shutil.copystat(info, temporary)
        stat = info.stat()
        if os.geteuid() == 0:
            os.chown(temporary, stat.st_uid, stat.st_gid)
        os.replace(temporary, info)
    finally:
        temporary.unlink(missing_ok=True)


PROBE = """import json,urllib.request
s=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/state',timeout=5))
print(json.dumps({'planner':s.get('standard_planner'),'live':s.get('live'),
'enabled':s.get('control',{}).get('enabled'),'health':s.get('health',{}).get('status'),
'connection':s.get('device_connection')}))
"""


def recover(app, system, runner, docker, enroll=False, attempts=36, pause=time.sleep):
    current = fingerprint(system)
    approval_file = app / "approved-nas-versions.json"
    approved = json.loads(approval_file.read_text()) if approval_file.exists() else []
    ready = docker_ready(runner, docker)
    if not ready:
        repair_metadata(system, app, current, approved)
        runner.run(
            [str(system / "usr/syno/bin/synopkg"), "start", "ContainerManager"],
            timeout=90,
        )
        for _ in range(attempts):
            if docker_ready(runner, docker):
                ready = True
                break
            pause(5)
        if not ready:
            raise RecoveryError(
                "Container Manager did not become ready; no reinstall or model spoofing attempted."
            )
    live = (app / "nas-live.enabled").exists()
    command = [
        docker,
        "--host",
        "unix:///var/run/docker.sock",
        "compose",
        "-p",
        "alphaess-household",
        "--project-directory",
        str(app),
        "--env-file",
        str(app / ".env"),
        "-f",
        str(app / "compose.nas.yaml"),
    ]
    if live:
        command += ["-f", str(app / "compose.nas.live.yaml")]
    # Never fetch new images or dependencies as part of recovery.
    runner.run(command + ["up", "-d", "--no-build", "--pull", "never"], timeout=90)
    observed = None
    for _ in range(attempts):
        try:
            observed = json.loads(
                runner.run(
                    [
                        docker,
                        "--host",
                        "unix:///var/run/docker.sock",
                        "exec",
                        "alphaess-household",
                        "python",
                        "-c",
                        PROBE,
                    ],
                    timeout=15,
                )
            )
            connection = observed.get("connection") or {}
            try:
                contact_age = (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(connection["last_success_at"])
                ).total_seconds()
            except (KeyError, ValueError, TypeError):
                contact_age = float("inf")
            if (
                observed.get("planner") == "emhass"
                and observed.get("live") is live
                and connection.get("status") == "connected"
                and 0 <= contact_age <= 300
                and observed.get("health") in ("active", "observing")
            ):
                break
        except (RecoveryError, ValueError, subprocess.TimeoutExpired):
            pass
        pause(5)
    else:
        raise RecoveryError(
            "Cannot enroll or confirm recovery: live cloud contact and a successful controller cycle were not verified; inspect container logs."
        )
    if enroll:
        if current not in approved:
            approved.append(current)
            atomic_json(approval_file, approved)
    # A new DSM version is acceptable while Docker works without system repairs.
    # It is never silently enrolled for future invasive repair.
    return {
        "status": "healthy",
        "version": current,
        "repair_approved": current in approved,
        "live": live,
        "enrolled": enroll,
    }


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)


REVIEWED_INSTALLER_SHA = "de015c4c168d9415ebcdcfd492a1449eeff71f9237a95ff76330772f648fb5ef"
UPSTREAM_REPO = "007revad/ContainerManager_for_all_armv8"


def fetch_upstream(url):
    request = urllib.request.Request(url, headers={"User-Agent": "AlphaESS-NAS-recovery"})
    with urllib.request.urlopen(request, timeout=15) as response:
        content = response.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        raise ValueError("Upstream response exceeds size limit")
    return content


def check_upstream(app, system, fetch=fetch_upstream):
    """Check the latest installer on every boot/recovery, without executing it.

    Persist DSM identity so a failed post-update check remains distinguishable
    from an earlier successful check. A failed request never reports up-to-date.
    """
    version = fields(system / "etc.defaults/VERSION")
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "dsm": {k: version.get(k) for k in ("productversion", "buildnumber", "smallfixnumber")},
        "reviewed_sha256": REVIEWED_INSTALLER_SHA,
        "executed": False,
    }
    try:
        commits = json.loads(fetch(
            "https://api.github.com/repos/" + UPSTREAM_REPO
            + "/commits?path=install_container_manager.sh&per_page=1"
        ))
        commit = commits[0]["sha"]
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Invalid upstream commit")
        digest = hashlib.sha256(fetch(
            "https://raw.githubusercontent.com/" + UPSTREAM_REPO + "/"
            + commit + "/install_container_manager.sh"
        )).hexdigest()
        result.update(
            status="up_to_date" if digest == REVIEWED_INSTALLER_SHA else "review_required",
            latest_commit=commit, latest_sha256=digest,
        )
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        result.update(status="check_failed", error=type(exc).__name__)
    path = app / "upstream-installer-status.json"
    previous = json.loads(path.read_text()) if path.exists() else {}
    atomic_json(path, result)
    # Repeated unchanged recovery runs do not repeat the same DSM notification.
    result["notify"] = result["status"] != "up_to_date" and any(
        previous.get(k) != result.get(k)
        for k in ("status", "dsm", "latest_commit")
    )
    return result


def notify_user(user, message):
    notify = Path("/usr/syno/bin/synodsmnotify")
    if user and notify.is_file():
        subprocess.run(
            [str(notify), user, "Energy manager needs attention", message],
            timeout=15, check=False,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--enroll", action="store_true")
    parser.add_argument("--notify-user")
    args = parser.parse_args()
    app = args.app_dir.resolve(strict=True)
    if os.geteuid() != 0:
        parser.error(
            "Run as root through DSM Task Scheduler or an administrator shell."
        )
    # Root tasks must not execute a Compose file or script editable by NAS users.
    protected = [app, Path(__file__).resolve(), app / "compose.nas.yaml", app / ".env"]
    protected += [
        app / name
        for name in (
            "compose.nas.live.yaml",
            "nas-live.enabled",
            "approved-nas-versions.json",
        )
        if (app / name).exists()
    ]
    for path in protected:
        stat = path.stat()
        if path.is_symlink() or stat.st_uid != 0 or stat.st_mode & 0o022:
            parser.error(
                "Deployment files must be root-owned and not group/world writable: "
                + str(path)
            )
    os.umask(0o077)
    with (app / ".nas-recovery.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            upstream = check_upstream(app, Path("/"))
            if upstream["notify"]:
                notify_user(args.notify_user, "Community installer: " + upstream["status"] + ". Review upstream-installer-status.json; no downloaded script executed.")
            docker = (
                shutil.which("docker")
                or "/var/packages/ContainerManager/target/usr/bin/docker"
            )
            result = recover(app, Path("/"), Runner(), docker, enroll=args.enroll)
            result["upstream_installer"] = upstream
            result["checked_at"] = datetime.now(timezone.utc).isoformat()
            atomic_json(app / "nas-recovery-status.json", result)
            print(json.dumps(result))
            return 0
        except (RecoveryError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            message = str(exc) if isinstance(exc, RecoveryError) else type(exc).__name__
            status_file = app / "nas-recovery-status.json"
            previous = (
                json.loads(status_file.read_text()) if status_file.exists() else {}
            )
            failure = {
                "status": "needs_attention",
                "message": message,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            atomic_json(status_file, failure)
            print("NAS recovery needs attention: " + message, file=sys.stderr)
            notify = Path("/usr/syno/bin/synodsmnotify")
            if (
                args.notify_user
                and notify.is_file()
                and (
                    previous.get("status") != "needs_attention"
                    or previous.get("message") != message
                )
            ):
                subprocess.run(
                    [
                        str(notify),
                        args.notify_user,
                        "Energy manager needs attention",
                        message,
                    ],
                    timeout=15,
                    check=False,
                )
            return 1


if __name__ == "__main__":
    sys.exit(main())
