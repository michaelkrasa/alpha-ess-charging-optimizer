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

From the development computer, with Docker running and authorized SSH access:

```bash
./scripts/deploy-nas.sh --host YOUR_USER@YOUR_NAS
# Optional custom SSH key:
./scripts/deploy-nas.sh --host YOUR_USER@YOUR_NAS --key ~/.ssh/id_ed25519
```

To keep future updates to `./scripts/deploy-nas.sh`, save `NAS_SSH_HOST=YOUR_USER@YOUR_NAS` in your private `.env`.

The updater currently expects `/volume1/docker/alphaess-household`, Compose project `alphaess-household`, root-owned configuration and a `nas-live.enabled` marker for live mode. It targets that commissioned layout, not every Docker host. Generic Compose installations can use their normal build/recreate process instead.

The command runs tests, builds Linux ARM64, smoke-tests the image without credentials/network access, transfers through SSH, backs up SQLite and recreates the container. A successful update requires a fresh controller cycle and cloud contact while preserving ON/OFF intent. A failed check restores the prior image and checks it again. If the prior controller is also unhealthy, the command reports failure; rollback is not proof of health.

The NAS administrator enters a password when `sudo` asks; neither the script nor the image stores it. For a temporary maintenance session, disable SSH afterwards. App updates do not require a DSM root task or a Container Manager installation workaround.

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
