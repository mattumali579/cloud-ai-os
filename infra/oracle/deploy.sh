#!/usr/bin/env bash
# =============================================================================
# Cloud AI OS — deploy latest code on the Oracle VM (ARCHITECTURE.md §14)
# git pull → compose build → up -d → health check. Idempotent.
#
# Usage (on the VM, any cwd):  ./infra/oracle/deploy.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/infra/docker/docker-compose.oracle.yml"
HEALTH_URL="${HEALTH_URL:-http://localhost:8080/healthz}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_SLEEP="${HEALTH_SLEEP:-5}"

log() { printf '==> %s\n' "$*"; }

cd "$REPO_ROOT"
[[ -f .env ]] || { echo "ERROR: $REPO_ROOT/.env missing — run bootstrap.sh first" >&2; exit 1; }

log "Pulling latest code (ff-only; the VM never carries local commits — code lives in GitHub, §1)"
git pull --ff-only

log "Building images"
docker compose -f "$COMPOSE_FILE" build

log "Restarting services"
docker compose -f "$COMPOSE_FILE" up -d

log "Health check: $HEALTH_URL"
for ((i = 1; i <= HEALTH_RETRIES; i++)); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
        log "HEALTHY: $(curl -fsS "$HEALTH_URL")"
        docker compose -f "$COMPOSE_FILE" ps
        exit 0
    fi
    printf '.'
    sleep "$HEALTH_SLEEP"
done

echo
echo "ERROR: deploy finished but $HEALTH_URL is not healthy — check logs:" >&2
echo "  docker compose -f $COMPOSE_FILE logs --tail 100 agent-api worker" >&2
exit 1
