#!/usr/bin/env bash
# Dump schema mctb from the Postgres container into a dated file and keep 14 days.
# CONTAINER=cloudos-db-1 PGUSER=cloudos PGDATABASE=cloudos ./scripts/backup.sh
# OUT_DIR defaults to products/missed-call-textback/backups
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CONTAINER="${CONTAINER:-cloudos-db-1}"
PGUSER="${PGUSER:-cloudos}"
PGDATABASE="${PGDATABASE:-cloudos}"
OUT_DIR="${OUT_DIR:-$ROOT/backups}"
DOCKER_BIN="${DOCKER:-docker}"

mkdir -p "$OUT_DIR"
STAMP=$(date +%Y%m%d-%H%M%S)
FILE="$OUT_DIR/mctb-$STAMP.sql"

"$DOCKER_BIN" exec "$CONTAINER" pg_dump -U "$PGUSER" -d "$PGDATABASE" --schema=mctb --no-owner --no-acl > "$FILE"

find "$OUT_DIR" -name 'mctb-*.sql' -type f -mtime +14 -delete

echo "Wrote $FILE"
