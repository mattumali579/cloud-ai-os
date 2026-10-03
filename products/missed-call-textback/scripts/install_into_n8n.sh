#!/usr/bin/env bash
# Apply schema mctb (when DATABASE_URL is set) and import the workflows into n8n.
# COMPOSE_FILE defaults to the Cloud AI OS stack. Override it for the dedicated product compose.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
REPO=$(cd "$ROOT/../.." && pwd)
COMPOSE_FILE="${COMPOSE_FILE:-$REPO/infra/docker/docker-compose.yml}"
SERVICE="${N8N_SERVICE:-n8n}"
DB_SERVICE="${DB_SERVICE:-db}"

if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "Applying schema to DATABASE_URL"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$ROOT/sql/001_schema.sql"
elif [[ "${APPLY_VIA_COMPOSE:-}" == "1" ]]; then
  echo "Applying schema through docker compose service $DB_SERVICE"
  docker compose -f "$COMPOSE_FILE" exec -T "$DB_SERVICE" \
    psql -U "${POSTGRES_USER:-cloudos}" -d "${POSTGRES_DB:-cloudos}" -v ON_ERROR_STOP=1 \
    < "$ROOT/sql/001_schema.sql"
else
  echo "Skipping schema. Set DATABASE_URL or APPLY_VIA_COMPOSE=1 to apply sql/001_schema.sql."
fi

echo "Copying workflows into $SERVICE"
docker compose -f "$COMPOSE_FILE" cp "$ROOT/workflows" "$SERVICE:/tmp/mctb-workflows"
docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" \
  n8n import:workflow --separate --input=/tmp/mctb-workflows

echo
echo "Imported. Workflows stay inactive until you attach the BrightReach Postgres credential and activate them."
echo "See RUNBOOK.md sections 3 and 4."
