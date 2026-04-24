# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

This is a **deployment-only repository** — it contains no installable Python packages of its own. It provides Docker Compose configurations, Dockerfiles, and provisioning scripts for running the Digital Slide Archive (DSA). The actual application code lives in upstream repos that are cloned and installed during the Docker build:

- [Girder](https://github.com/girder/girder) — asset/user management and REST API
- [HistomicsUI](https://github.com/DigitalSlideArchive/HistomicsUI) — annotation UI
- [large_image](https://github.com/girder/large_image) — tiled image access
- [girder_worker](https://github.com/girder/girder_worker) — Celery-based task runner
- [slicer_cli_web](https://github.com/girder/slicer_cli_web) — Docker task execution

## Deployment Variants

| Directory | Image | Python | Cache | Notes |
|---|---|---|---|---|
| `devops/dsa/` | `dsarchive/dsa_common` | 3.11 | Memcached | Standard/primary deployment |
| `devops/ver5/` | `dsarchive/dsa_common_5` | 3.13 | Redis | Next-gen (Girder 5.x) |
| `devops/minimal/` | `dsarchive/dsa_minimal` | — | Redis | No worker/RabbitMQ |
| `devops/external-worker/` | `dsarchive/dsa_common` | — | — | Server and worker on separate machines (uses Docker profiles) |
| `devops/with-dive-volview/` | — | — | — | Override file adding DIVE and VolView to the standard deployment |

## Key Commands

### Start / Stop (standard deployment)
```bash
cd devops/dsa/
DSA_USER=$(id -u):$(id -g) docker compose up        # foreground
DSA_USER=$(id -u):$(id -g) docker compose up -d     # background
docker compose down -v                               # stop and remove ephemeral volumes
```

`DSA_USER` must be set to ensure files (db, logs, assetstore) are owned by the current user, not root.

### Build
```bash
# Build only (from devops/dsa/ or devops/ver5/)
DSA_USER=$(id -u):$(id -g) docker compose build girder

# Build ver5 image
cd devops/ver5/
DSA_USER=$(id -u):$(id -g) docker compose build girder
```

### Shell into running containers
```bash
docker compose exec girder bash
docker compose exec --user $(id -u) girder bash    # as your own user
docker compose exec girder bash -lc 'restart_girder.sh'
docker compose exec girder bash -lc 'rebuild_and_restart_girder.sh'
```

### Run tests (inside the girder container)
```bash
# Run all tox environments
docker exec dsa-girder-1 bash -lc 'PYTEST_NUMPROCESSES=4 tox -e lint,lintclient,py310,py313'

# CLI integration test (requires running instance + pip install girder-client)
python3 devops/dsa/utils/cli_test.py dsarchive/histomicstk:latest --test
# or from inside container:
python /opt/digital_slide_archive/devops/dsa/utils/cli_test.py dsarchive/histomicstk:latest --test --username=admin --password=password
```

### Verify provisioning defaults
```bash
pip install pyyaml
python ./devops/dsa/provision.py --dry-run > /tmp/defaults.yaml
python ./devops/dsa/provision.py --dry-run --no-defaults --yaml=devops/dsa/provision.yaml > /tmp/provfile.yaml
diff /tmp/defaults.yaml /tmp/provfile.yaml
```

### Database backup / restore
```bash
docker compose exec mongodb /usr/bin/mongodump --db girder --archive --gzip > dsa_girder.dump.gz
docker compose exec -T mongodb /usr/bin/mongorestore --db girder --archive --gzip --drop < dsa_girder.dump.gz
```

### Linting
```bash
pre-commit run --all-files
```

## Provisioning System

`devops/dsa/provision.py` runs at container startup (before Girder starts). It reads `provision.yaml` to:
- Create a default admin user (`admin`/`password`)
- Create a default filesystem assetstore at `/assetstore`
- Set Girder plugin settings (worker broker URL, HistomicsUI paths, etc.)
- Pull and register Slicer CLI Docker images (e.g., `dsarchive/histomicstk:latest`)
- Optionally install additional pip packages and rebuild the Girder web client

The active provision file is set via `DSA_PROVISION_YAML` environment variable (default: `/opt/digital_slide_archive/devops/dsa/provision.yaml`).

## Customization Pattern

Create a `docker-compose.override.yml` alongside the active `docker-compose.yml` and optionally a `provision.local.yaml`:

```yaml
# docker-compose.override.yml
services:
  girder:
    environment:
      DSA_PROVISION_YAML: /opt/digital_slide_archive/devops/dsa/provision.yaml
    volumes:
      - ./provision.local.yaml:/opt/digital_slide_archive/devops/dsa/provision.yaml
      - /mnt/data:/mnt/data
```

Useful `provision.yaml` options: `pip` (extra packages), `rebuild-client` (True/"development"), `slicer-cli-image`/`slicer-cli-image-pull`, `settings` (arbitrary Girder settings), `resources` (create collections/folders).

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `DSA_USER` | (required) | `uid:gid` for file ownership |
| `DSA_PORT` | `8080` | Host port for the Girder UI |
| `DSA_PROVISION_YAML` | path inside container | Active provision file |
| `DSA_GIRDER_MOUNT_OPTIONS` | — | Options for `girder mount` (e.g., diskcache) |
| `DSA_WORKER_CONCURRENCY` | `2` | Celery worker concurrency |
| `DOCKER_CONFIG` | — | Path to docker auth config (for private registries) |

## External Worker (Separate Machines)

Use `devops/external-worker/` with Docker Compose profiles:
```bash
# On the server machine:
DSA_USER=$(id -u):$(id -g) docker compose --profile server up --build -d
# On each worker machine:
DSA_USER=$(id -u):$(id -g) docker compose --profile worker up --build -d
```
RabbitMQ port 5672 is exposed externally in this configuration.

## CI (CircleCI)

The pipeline builds both `dsa_common` (Girder 3.x) and `dsa_common_5` (Girder 5.x) images, runs CLI/proxy/UI tests inside the built containers, scans with Trivy for HIGH/CRITICAL CVEs, and publishes to Docker Hub on `master` and version tags. A `build-tracking` branch records upstream dependency commit hashes from successful builds.
