# Household Synology deployment

## Running service

- Dashboard: **http://nasnas.local:8765/** or **http://10.0.0.11:8765/**.
- Container: `alphaess-household`; Compose project: `alphaess-household`.
- Application directory: `/volume1/docker/alphaess-household`.
- Persistent database: `data/manager.sqlite3` inside that directory.
- Active planner: EMHASS; live capability enabled through `compose.nas.live.yaml`. Dashboard control is a separate persisted setting.
- The previous Mac service was stopped before the NAS started. Never start two live controllers.

This is a LAN service with no dashboard sign-in. It still communicates with the inverter through the AlphaESS cloud API. No direct inverter LAN/Modbus connection has been configured.

## Hardware and commissioning

Commissioned on 22 October 2026:

| Item | Verified value |
| --- | --- |
| Model | RS819 |
| CPU | Realtek RTD1296, ARM64, four cores at 1.4 GHz |
| RAM | 2 GB |
| DSM | 7.4.1-90080 Update 1 |
| Container Manager | 24.0.2-1706 |
| Python in container | 3.12 |
| EMHASS | 0.18.6 |
| Container memory limit | 1 GiB |

RS819 is excluded from the official Container Manager installation route. The owner explicitly authorized the reviewed [community workaround](https://github.com/007revad/ContainerManager_for_all_armv8). Installation used the official [ARM64 SPK](https://archive.synology.com/download/Package/ContainerManager/24.0.2-1706) and checksum-pinned installer source. Temporary model changes were restored and checked in both Synology configuration files.

The initial server-download route stopped at the manual fallback. The successful route supplied the pinned official SPK locally through the same reviewed workaround. This is an unsupported installation; successful operation on this exact version does not establish compatibility with future DSM versions.

An isolated, network-disabled EMHASS solve on the actual NAS passed in **1.24 seconds**, with peak process RSS **196,764 KiB** (about 192 MiB). The 16-interval fixture verified charging under a low/high price spread and the 10% reserve. This is a solver smoke test, not a financial backtest or full runtime memory ceiling. A real forecast cycle also completed on the NAS with fresh cloud readings. Container Manager showed approximately 216 MB for the running Python process, with total NAS RAM around 41%. The container was restarted through Container Manager to verify persistent ON intent and recovery.

The image was built for `linux/arm64`, loaded locally and tagged `alphaess-household:local`. EMHASS, NumPy, pandas, CVXPY and HiGHS versions match the validated stack. No credentials are embedded in the image. The staged SQLite snapshot was created through SQLite backup, with control OFF and stale health/lease records removed. Control was enabled only after the Mac stopped and fresh NAS observation succeeded.

## Container operation

`Dockerfile.nas` is the regular server image. The original `Dockerfile` targets Lambda. Runtime configuration:

- `.env`: `APP_ID`, `APP_SECRET`, `SERIAL_NUMBER`, `NAS_BIND_IP`.
- `manager.yaml`: planner, reserve, battery model, price source and verified HDO validity.
- `solar_config.yaml`: solar location/geometry.
- Base Compose: monitoring/planning only.
- Live override: passes `--live`; persisted control still determines whether schedules may change.

From an administrator shell:

```bash
cd /volume1/docker/alphaess-household
sudo docker compose --env-file .env -f compose.nas.yaml -f compose.nas.live.yaml ps
sudo docker logs --tail 100 alphaess-household
sudo docker stats --no-stream alphaess-household
```

The deployment directory and credentials are root-owned and private. The data directory is owned by UID/GID 10001, the unprivileged container account. The container has a read-only root, writable temporary storage, dropped capabilities, no-new-privileges, bounded logs and `restart: unless-stopped`.

To stop/start deliberately:

```bash
sudo docker compose --env-file .env -f compose.nas.yaml -f compose.nas.live.yaml stop
sudo docker compose --env-file .env -f compose.nas.yaml -f compose.nas.live.yaml up -d --no-build --pull never
```

Turning dashboard control OFF requests native inverter settings; stopping the container alone does not cancel installed schedules. A cloud outage also does not clear repeating schedules.

## Boot recovery and DSM updates

The root boot hook is `/usr/local/etc/rc.d/S99alphaess-recover.sh`. It waits 60 seconds and invokes `ops/nas/recover.py`. The recovery command was run successfully again after installation to test idempotence. **An actual NAS reboot and a future DSM upgrade have not been tested.** DSM may change or remove custom startup hooks during upgrades; automatic operation cannot be guaranteed across arbitrary future releases.

Recovery:

1. Checks upstream GitHub for the newest commit affecting the community installer, fetches that immutable script and compares its SHA-256 with the reviewed copy. It does not execute downloaded code.
2. Verifies the physical NAS model and restored identifiers.
3. If Docker is already working, starts the existing local image without downloading or rebuilding it.
4. If Docker needs repair, permits only explicitly commissioned DSM/package pairs. It backs up package INFO and removes only the model-exclusion field, then starts Container Manager.
5. Waits for HTTP, the configured EMHASS planner, fresh AlphaESS cloud contact and a successful controller cycle.

An unknown broken DSM/package combination stops for administrator review; it is never silently enrolled. A working new version may run the existing container, but is not automatically approved for future system-file repairs. The approved current pair is recorded in `approved-nas-versions.json` after actual NAS commissioning. Root ownership and write permissions are checked before running Compose.

Status files in the application directory:

| File | Meaning |
| --- | --- |
| `nas-recovery-status.json` | Recovery outcome and tested version identity |
| `upstream-installer-status.json` | Upstream check time, DSM identity, commit and hashes |
| `boot-recovery.log` | Boot invocation output |
| `recovery-backups/` | Original INFO files before bounded repairs |
| `nas-live.enabled` | Explicit marker allowing the live Compose override |

Changed upstream scripts, failed version checks and recovery failures produce deduplicated local DSM notifications to the administrator. They do not automatically update the installer or the application image. At commissioning, the newest installer-file commit was `dbcb7ef182aa0cdbc533f41bd7697f2f6019b32e`; its script checksum matched the reviewed copy `de015c4c168d9415ebcdcfd492a1449eeff71f9237a95ff76330772f648fb5ef`.

Manual recovery, when SSH is enabled:

```bash
sudo /usr/bin/python3 /volume1/docker/alphaess-household/ops/nas/recover.py \
  --app-dir /volume1/docker/alphaess-household --notify-user misa
```

Review a new DSM/package combination before using `--enroll`. That flag requires fresh cloud contact and a completed controller cycle; it is not a compatibility bypass.

## Security and access

The active DSM firewall profile allows TCP/22 only from the commissioning Mac's current IPv4 address `10.0.0.182`, followed by a deny rule for all other sources on all interfaces. Fresh IPv4 SSH key login succeeded; an IPv6 link-local path could reach DSM but timed out on SSH. No globally routable NAS IPv6 address was present. Update the allow rule if the Mac's DHCP address changes.

Auto Block was already enabled (five failures in five minutes, seven-day expiry), as was LAN1 DoS protection. The existing Mac public key is authorized for `misa`; the private key stays on the Mac. Sudo still requires administrator authentication. Temporary SSH is disabled after commissioning; re-enable it through DSM only when maintenance requires it.

DSM listed no managed router port forwards. **Actual router WAN forwarding, UPnP, DMZ and upstream firewall configuration remain unverified**, because the owner deferred router review. Existing QuickConnect remains enabled. NAS filtering cannot prevent a volumetric DDoS attack from saturating the household internet connection. No claim of complete external isolation or external-WAN testing is made.

Docker-published ports need their own exposure review; a DSM SSH rule is not proof that Docker forwarding is filtered. Keep 8765 off the router's public port-forward list. The dashboard has no local authentication by household choice.

## Backups, updates and rollback

Back up `manager.yaml`, `solar_config.yaml`, the private `.env`, recovery approval files and SQLite state. Use SQLite's backup API or stop the container first; do not copy only an open WAL database file. Never commit these private runtime records or the deployment archive.

For an application update, first review and test changes, build a new ARM64 image and retain the prior image tag. Stop the existing container before replacement so there is one writer. Recheck cloud freshness, selected planner and schedule readbacks after starting. Keep the database for rollback; changing an image tag alone must not reset ON/OFF intent or write history.

If the NAS cannot run the controller, keep its container stopped before restarting the Mac with `main.py serve --live`. Installed inverter schedules remain until successfully changed; check them during any recovery.
