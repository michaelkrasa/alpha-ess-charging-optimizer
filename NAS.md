# Docker / NAS deployment

Run the web server and controller together, with persistent SQLite on a local disk. Home Assistant is optional. Use a supported Container Manager / Docker installation; an SSH terminal alone does not imply that a NAS supports containers.

## First installation

1. Clone the repository. Follow [setup](docs/SETUP.md) to create `.env`, `manager.local.yaml` and `solar.local.yaml`.
2. Add these paths to `.env`:

   ```dotenv
   MANAGER_CONFIG=./manager.local.yaml
   SOLAR_CONFIG=./solar.local.yaml
   # Set to the server's trusted LAN address for other devices to connect.
   NAS_BIND_IP=127.0.0.1
   ```

3. Prepare the persistent directory and start in observation mode:

   ```bash
   mkdir -p data
   sudo chown 10001:10001 data
   chmod 600 .env
   docker compose --env-file .env -f compose.nas.yaml up -d --build
   ```

4. Open `http://YOUR_SERVER:8765`, check fresh cloud contact and review forecasts, tariff and readiness messages. Complete [commissioning](docs/SETUP.md#before-live-control) before adding the live override:

   ```bash
   docker compose --env-file .env -f compose.nas.yaml -f compose.nas.live.yaml up -d
   ```

5. Enable management in the dashboard. Keep one live instance per inverter.

Synology Container Manager can import the Compose project instead. The image uses standard Linux Python; `Dockerfile.nas` is the long-running server image despite its name. A 64-bit Raspberry Pi is a candidate for this path, but solver performance on the actual hardware must be checked before live operation. This repository does not certify every NAS or Pi model.

## Updates to a commissioned Synology

Updates are deliberate deployments of a tested container image; a Git push or Dependabot PR does not change the running NAS service. The source is built on the development computer, then the NAS container is replaced while its bind-mounted database, private credentials and installation settings remain in place.

### One-time authorization

Enable SSH in DSM **Control Panel → Terminal & SNMP**. Keep it reachable only from your trusted deployment computer using the NAS firewall; remove any broader SSH service allow rule that DSM adds when enabling SSH. Use a reserved LAN address and no router port forwarding. Save `NAS_SSH_HOST=YOUR_USER@YOUR_NAS` in your private `.env`, then:

```bash
./scripts/deploy-nas.sh --setup
```

Enter the NAS administrator password once. This installs a root-owned updater and a narrow sudo rule: only `/usr/local/sbin/alphaess-update` **with no arguments** is passwordless. The helper accepts a bounded image upload over stdin, validates its SHA-256 and Docker image identity, and uses the existing root-owned Compose configuration. It does not execute uploaded Python, accept shell commands or grant general passwordless sudo. The deployment computer and its SSH key are trusted to replace this app; protect that key.

The installer checks that DSM includes `/etc/sudoers.d` and uses `visudo` when available. It then switches to the deployment account, ignores cached authentication and invokes a harmless privileged updater probe. This tests the real passwordless path even on DSM installations without `visudo`; a failed probe restores the previous rule. If `sudoers.d` is not included, setup stops instead of editing the main sudo policy. It does not enable SSH or alter firewall rules. Check the helper after DSM updates; reinstall with `--setup` if removed. Changes to the privileged updater itself also require rerunning setup deliberately.

### Every update afterwards

Start Docker on the development computer, then:

```bash
./scripts/deploy-nas.sh
```

There is no password prompt. The command runs tests, builds Linux ARM64, smoke-tests the image without credentials/network access, streams it through SSH, backs up SQLite and recreates the container. A successful update requires a fresh controller cycle and cloud contact while preserving ON/OFF intent. A failed check restores the prior image and checks it again. If the prior controller is also unhealthy, the command reports failure; rollback is not proof of health.

The dashboard footer shows the image revision (with `-dirty` for local source changes) and the last verified deployment time in Prague time. The updater records that time only after the new controller passes its health check. Older images or deployments without matching metadata show the time as unavailable. After updating `ops/nas/update.py`, deliberately reinstall the privileged helper with `./scripts/deploy-nas.sh --setup` before the next deployment so it can record this metadata.

A Git push does not deploy automatically. Pull reviewed changes before deploying a newer release. To specify the connection explicitly:

```bash
./scripts/deploy-nas.sh --host YOUR_USER@YOUR_NAS --key ~/.ssh/id_ed25519
```

If the deployment computer has both Ethernet and Wi-Fi, its route to the NAS may use a different source address from the one allowed by the firewall. Set optional `NAS_SSH_BIND_ADDRESS` in the private `.env`, or pass `--bind-address`, to use a specific local address already allowed by the NAS. That address must still be assigned to the computer. This avoids broadening firewall rules when switching interfaces.

This unattended path leaves LAN-restricted SSH enabled. If you prefer disabling SSH between updates, enable it before running the command and disable it afterwards; the installed helper still removes repeated sudo prompts. To revoke passwordless deployment, remove `/etc/sudoers.d/alphaess-update` as administrator and remove the helper/install directory if no longer needed.

The updater currently expects `/volume1/docker/alphaess-household`, Compose project `alphaess-household`, root-owned configuration and a `nas-live.enabled` marker for live mode. It targets that commissioned layout, not every Docker host. Generic Compose installations can use their normal build/recreate process instead. The restricted helper and unattended update path have been verified on the commissioned DSM installation. Validate them on each new target host.

### GUI import alternative

```bash
./scripts/deploy-nas.sh --image-only
```

Import `dist/nas/alphaess.tar.gz` through **Container Manager → Image → Action → Import → Add From File → From local device**. Keep a known-good image. Stop the existing container, reset it to the imported image and start it with the existing mounts. Verify fresh telemetry and controller status. This manual path does not execute the SSH updater's automated rollback.

## Data and credentials

| Item | Handling |
| --- | --- |
| `.env` | Host-private file (`0600`); credentials injected at runtime |
| `manager.local.yaml`, `solar.local.yaml` | Read-only bind mounts; installation-specific and Git-ignored |
| `data/manager.sqlite3` | Persistent SQLite, writable by UID/GID 10001 |
| Image | Contains application and dependencies; no API credentials |

Docker administrators can inspect container environment values. Runtime environment variables protect secrets from image layers and Git, not from the host administrator. Back up through SQLite's backup API or stop the container before copying the database; a live file copy can omit WAL contents. Avoid network-mounted SQLite files.

The container drops capabilities, uses a read-only root filesystem, bounded logs and writable temporary storage. Its HTTP healthcheck checks server availability; the dashboard separately reports controller and cloud health.

## NAS and DSM updates

Prefer a model with official Container Manager support. `ops/nas/deploy.py`, `install-container-manager.sh` and `recover.py` contain an installation-specific, unsupported commissioning/recovery path. They are **not** general installation instructions. Recovery checks approved DSM/package identities and reports changed upstream installers; it does not automatically execute newly downloaded root scripts. Test a backup and recovery path before depending on an unsupported installation.

Keep the dashboard on a trusted LAN. Use a VPN or an authenticated reverse proxy for remote access; the local dashboard deliberately has no login. Router forwarding and WAN firewall settings are separate from container security.
