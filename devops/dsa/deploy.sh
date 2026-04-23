#!/usr/bin/env bash
# deploy.sh — build and (re)deploy local development images for DSA
#
# Repos are assumed to be siblings under a common parent directory:
#   <code>/digital_slide_archive   (this repo)
#   <code>/histomicstk             (HistomicsTK + Slicer CLI image)
#   <code>/TRIDENT                  (TRIDENT WSI embedding library)
#   <code>/HistomicsUI              (HistomicsUI front-end, mounted at runtime)
#
# Usage:
#   ./deploy.sh [--no-build] [--restart]
#
#   --no-build   skip image build, just register the existing image in DSA
#   --restart    bring the DSA stack down and back up after building

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
ENV_FILE="${CODE_DIR}/digital_slide_archive/.env"

HTK_DIR="${CODE_DIR}/histomicstk"
TRIDENT_DIR="${CODE_DIR}/TRIDENT"
DSA_DIR="${SCRIPT_DIR}"

BUILD=true
RESTART=false

for arg in "$@"; do
    case "${arg}" in
        --no-build) BUILD=false ;;
        --restart)  RESTART=true ;;
        *) echo "Unknown argument: ${arg}"; exit 1 ;;
    esac
done

# ── Read admin password from .env ─────────────────────────────────────────────
ADMIN_PASSWORD=""
if [[ -f "${ENV_FILE}" ]]; then
    ADMIN_PASSWORD="$(grep -E '^DSA_ADMIN_PASSWORD=' "${ENV_FILE}" | cut -d= -f2-)"
fi
if [[ -z "${ADMIN_PASSWORD}" ]]; then
    echo "ERROR: DSA_ADMIN_PASSWORD not found in ${ENV_FILE}"; exit 1
fi

# ── Build dsarchive/histomicstk:dev ───────────────────────────────────────────
if [[ "${BUILD}" == "true" ]]; then
    echo "==> Building dsarchive/histomicstk:dev"
    echo "    histomicstk source : ${HTK_DIR}"
    echo "    TRIDENT source     : ${TRIDENT_DIR}"

    if [[ ! -d "${HTK_DIR}" ]]; then
        echo "ERROR: histomicstk directory not found at ${HTK_DIR}"; exit 1
    fi
    if [[ ! -d "${TRIDENT_DIR}" ]]; then
        echo "ERROR: TRIDENT directory not found at ${TRIDENT_DIR}"; exit 1
    fi

    docker buildx build \
        --build-context trident="${TRIDENT_DIR}" \
        --tag dsarchive/histomicstk:dev \
        --load \
        "${HTK_DIR}"

    echo "==> Build complete: dsarchive/histomicstk:dev"
fi

# ── Optionally restart the DSA stack ──────────────────────────────────────────
if [[ "${RESTART}" == "true" ]]; then
    echo "==> Restarting DSA stack"
    cd "${DSA_DIR}"
    DSA_USER="$(id -u):$(id -g)" docker compose down
    DSA_USER="$(id -u):$(id -g)" docker compose up -d
    echo "==> DSA stack is up"
fi

# ── Register image in running DSA ─────────────────────────────────────────────
echo "==> Registering dsarchive/histomicstk:dev in DSA"
docker compose -f "${DSA_DIR}/docker-compose.yml" exec girder bash -lc \
    "python -c \"
import girder_client
gc = girder_client.GirderClient(apiUrl='http://localhost:8080/api/v1')
gc.authenticate('admin', '${ADMIN_PASSWORD}')
gc.put('slicer_cli_web/docker_image', parameters={'name': 'dsarchive/histomicstk:dev', 'pull': 'false'})
print('Registered dsarchive/histomicstk:dev')
\""

echo "==> Done."
