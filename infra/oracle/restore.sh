#!/usr/bin/env bash
# =============================================================================
# Cloud AI OS — restore local state from a backup.sh archive (ARCHITECTURE.md §14)
#
# Inverse of backup.sh: restores .env, the n8n data volume, and data/ onto a
# (usually fresh) VM. Called by bootstrap.sh when BACKUP_ARCHIVE is set, or run
# by hand. Run it BEFORE `compose up` (or stop services first) so n8n does not
# write over the volume mid-restore.
#
# Usage:  ./infra/oracle/restore.sh /path/to/cloudos-backup-<stamp>.tar.gz
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
N8N_VOLUME="${N8N_VOLUME:-cloudos_n8n-data}"
COMPOSE_FILE="$REPO_ROOT/infra/docker/docker-compose.oracle.yml"

log() { printf '==> %s\n' "$*"; }

ARCHIVE="${1:?usage: restore.sh <cloudos-backup-*.tar.gz>}"
[[ -f "$ARCHIVE" ]] || { echo "ERROR: archive not found: $ARCHIVE" >&2; exit 1; }

cd "$REPO_ROOT"

# If the stack is running, stop n8n so we do not restore under a live writer.
if docker compose -f "$COMPOSE_FILE" ps --status running 2>/dev/null | grep -q n8n; then
    log "Stopping running n8n before restore"
    docker compose -f "$COMPOSE_FILE" stop n8n
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

log "Unpacking $ARCHIVE"
tar xzf "$ARCHIVE" -C "$STAGE"
[[ -f "$STAGE/MANIFEST" ]] && { log "Manifest:"; sed 's/^/    /' "$STAGE/MANIFEST"; }

# 1. .env
if [[ -f "$STAGE/dotenv" ]]; then
    if [[ -f .env ]]; then
        log "Existing .env found — keeping a copy at .env.pre-restore"
        cp .env .env.pre-restore
    fi
    cp "$STAGE/dotenv" .env
    chmod 600 .env
    log "Restored .env"
else
    log "Archive has no .env — leaving current one in place"
fi

# 2. n8n volume
if [[ -f "$STAGE/n8n-data.tar.gz" ]]; then
    log "Restoring n8n volume '$N8N_VOLUME' (existing contents are replaced)"
    docker volume create "$N8N_VOLUME" >/dev/null
    docker run --rm \
        -v "$N8N_VOLUME":/data \
        -v "$STAGE":/backup:ro \
        alpine sh -c 'rm -rf /data/* /data/..?* /data/.[!.]* 2>/dev/null; tar xzf /backup/n8n-data.tar.gz -C /data'
    log "n8n volume restored"
else
    log "Archive has no n8n-data.tar.gz — skipping n8n volume"
fi

# 3. data/
if [[ -f "$STAGE/data.tar.gz" ]]; then
    log "Restoring data/"
    tar xzf "$STAGE/data.tar.gz" -C "$REPO_ROOT"
fi

log "Restore complete. Start (or restart) services with:"
log "  docker compose -f infra/docker/docker-compose.oracle.yml up -d --build"
