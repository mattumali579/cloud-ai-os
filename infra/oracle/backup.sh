#!/usr/bin/env bash
# =============================================================================
# Cloud AI OS — local-state backup (ARCHITECTURE.md §14)
#
# What needs backing up from the VM is ONLY what lives NOWHERE else (§1):
#   - n8n data volume (cloudos_n8n-data: workflows, credentials, SQLite DB)
#   - .env             (secrets — never committed anywhere)
#   - data/            (ad-hoc local data dir, if present)
#
# Everything else already survives the VM by design:
#   CODE → GitHub · STRUCTURED STATE → Supabase · KNOWLEDGE → Second Brain git repo.
# No pg_dump here: there is no local postgres on Oracle.
#
# Output: backups/cloudos-backup-<UTC timestamp>.tar.gz
#
# OFFSITE (do this — a backup on a disposable VM is not a backup):
#   - OCI Object Storage (Always Free ≤ 20 GB):
#       oci os bucket create --name cloudos-backups --compartment-id "$COMPARTMENT_ID"
#       oci os object put --bucket-name cloudos-backups --file backups/<archive>
#   - or scp to your laptop / a PRIVATE git repo (never a public one — the
#     archive contains .env and n8n credentials).
#
# Usage (on the VM):  ./infra/oracle/backup.sh
# Cron example (03:15 UTC daily):
#   15 3 * * * /home/ubuntu/cloud-ai-os/infra/oracle/backup.sh >> /home/ubuntu/backup.log 2>&1
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"
N8N_VOLUME="${N8N_VOLUME:-cloudos_n8n-data}"   # compose project 'cloudos' + volume 'n8n-data'
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$BACKUP_DIR/cloudos-backup-$STAMP.tar.gz"
KEEP_LAST="${KEEP_LAST:-7}"                    # prune old local archives (offsite copies keep history)

log() { printf '==> %s\n' "$*"; }

cd "$REPO_ROOT"
mkdir -p "$BACKUP_DIR"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# 1. n8n volume → tar (read-only mount; safe while n8n runs — SQLite may be
#    mid-write, so for a guaranteed-consistent copy stop n8n first:
#    docker compose -f infra/docker/docker-compose.oracle.yml stop n8n).
if docker volume inspect "$N8N_VOLUME" >/dev/null 2>&1; then
    log "Archiving n8n volume '$N8N_VOLUME'"
    docker run --rm \
        -v "$N8N_VOLUME":/data:ro \
        -v "$STAGE":/backup \
        alpine tar czf /backup/n8n-data.tar.gz -C /data .
else
    log "WARNING: volume '$N8N_VOLUME' not found — skipping n8n state"
fi

# 2. .env
if [[ -f .env ]]; then
    log "Including .env"
    cp .env "$STAGE/dotenv"
else
    log "WARNING: .env not found — skipping"
fi

# 3. Any local data/ directory
if [[ -d data ]]; then
    log "Including data/"
    tar czf "$STAGE/data.tar.gz" data
fi

# 4. Manifest + final archive
{
    echo "created_utc=$STAMP"
    echo "host=$(hostname)"
    echo "git_rev=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "contents=$(ls "$STAGE" | tr '\n' ' ')"
} > "$STAGE/MANIFEST"

tar czf "$ARCHIVE" -C "$STAGE" .
chmod 600 "$ARCHIVE"
log "Backup written: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

# 5. Prune old local archives
if compgen -G "$BACKUP_DIR/cloudos-backup-*.tar.gz" >/dev/null; then
    ls -1t "$BACKUP_DIR"/cloudos-backup-*.tar.gz | tail -n +$((KEEP_LAST + 1)) | while read -r old; do
        log "Pruning old local backup: $old"
        rm -f "$old"
    done
fi

log "REMINDER: copy the archive offsite (OCI Object Storage / scp / private git) —"
log "this VM is disposable. Supabase + GitHub + the Second Brain repo already hold canonical state."
