#!/usr/bin/env bash
# =============================================================================
# Cloud AI OS — fresh-VM bootstrap (Ubuntu ARM64, Oracle A1) — ARCHITECTURE.md §14
#
# Fresh VM → Docker → clone repo → .env → (optional) restore backup → compose up
# → health check. Idempotent: safe to re-run on a VM that is already set up.
#
# Usage (on the VM):
#   REPO_URL=https://github.com/<owner>/cloud-ai-os.git ./bootstrap.sh
#   REPO_URL=... BACKUP_ARCHIVE=/home/ubuntu/cloudos-backup-20260828.tar.gz ./bootstrap.sh
#
# The ONLY interactive step is filling .env on first run (documented in §14).
# Set NONINTERACTIVE=1 to skip the prompt (e.g. when .env is delivered by
# restore.sh or copied in beforehand) — bootstrap then fails if .env is missing.
# =============================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/cloud-ai-os}"
REPO_URL="${REPO_URL:-}"
BACKUP_ARCHIVE="${BACKUP_ARCHIVE:-}"
COMPOSE_FILE="infra/docker/docker-compose.oracle.yml"
HEALTH_URL="${HEALTH_URL:-http://localhost:8080/healthz}"
HEALTH_RETRIES="${HEALTH_RETRIES:-60}"
HEALTH_SLEEP="${HEALTH_SLEEP:-5}"

log() { printf '==> %s\n' "$*"; }

# ---------------------------------------------------------------- 1. Docker --
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Docker + compose plugin already installed: $(docker --version)"
else
    log "Installing Docker Engine (official convenience script; supports arm64)"
    sudo apt-get update -y
    sudo apt-get install -y --no-install-recommends ca-certificates curl git
    curl -fsSL https://get.docker.com | sudo sh
    sudo systemctl enable --now docker
fi

if ! id -nG "$USER" | grep -qw docker; then
    log "Adding $USER to the docker group"
    sudo usermod -aG docker "$USER"
    # Group membership needs a new login shell; use sudo for the rest of THIS run.
    DOCKER="sudo docker"
else
    DOCKER="docker"
fi

# ------------------------------------------------------------------ 2. Code --
if [[ -d "$APP_DIR/.git" ]]; then
    log "Repo already present at $APP_DIR (leaving working tree as-is; use deploy.sh to update)"
else
    [[ -n "$REPO_URL" ]] || { echo "ERROR: REPO_URL is required for first bootstrap" >&2; exit 1; }
    log "Cloning $REPO_URL → $APP_DIR"
    git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

# ------------------------------------------------------------------- 3. .env --
# Restore FIRST if a backup archive was provided — it contains .env + n8n state.
if [[ -n "$BACKUP_ARCHIVE" ]]; then
    log "Restoring from backup archive: $BACKUP_ARCHIVE"
    bash ./infra/oracle/restore.sh "$BACKUP_ARCHIVE"
fi

if [[ ! -f .env ]]; then
    log "Creating .env from .env.example"
    cp .env.example .env
    chmod 600 .env
    if [[ "${NONINTERACTIVE:-0}" == "1" ]]; then
        echo "ERROR: .env was just created from the template and NONINTERACTIVE=1 —" >&2
        echo "       fill it (DATABASE_URL=Supabase, ENVIRONMENT=oracle, tokens) and re-run." >&2
        exit 1
    fi
    cat <<'EOM'

  ┌─────────────────────────────────────────────────────────────────────┐
  │ ACTION REQUIRED: fill .env before services start. At minimum:       │
  │   ENVIRONMENT=oracle                                                │
  │   INSTANCE_NAME=cloudos-oracle                                      │
  │   DATABASE_URL=<Supabase connection string>   (NO local db here)    │
  │   AGENT_API_TOKEN=<long random>                                     │
  │   N8N_BASIC_AUTH_USER / N8N_BASIC_AUTH_PASSWORD                     │
  └─────────────────────────────────────────────────────────────────────┘

EOM
    read -r -p "Press ENTER to edit .env now (opens ${EDITOR:-nano})... "
    "${EDITOR:-nano}" .env
else
    log ".env already present — not touching it"
    chmod 600 .env || true
fi

# ------------------------------------------------------ 3b. Runtime secrets --
# Replace blank/template credentials with random VM-local values. Hex output
# keeps KEY=value replacement safe. Secrets remain in chmod-600 .env only.
ensure_runtime_secret() {
    local key="$1"
    local current=""
    current="$(sed -n "s/^${key}=//p" .env | head -n 1)"
    if [[ -n "$current" && ! "$current" =~ ^(change-me|replace-me|placeholder)$ ]]; then
        return
    fi

    local generated
    generated="$(openssl rand -hex 32)"
    if grep -q "^${key}=" .env; then
        sed -i "s/^${key}=.*/${key}=${generated}/" .env
    else
        printf '\n%s=%s\n' "$key" "$generated" >> .env
    fi
    log "Generated $key in VM-local .env"
}

ensure_runtime_secret AGENT_API_TOKEN
ensure_runtime_secret N8N_BASIC_AUTH_PASSWORD
ensure_runtime_secret N8N_ENCRYPTION_KEY
chmod 600 .env

# ------------------------------------------------------------- 4. Compose up --
log "Building and starting the Oracle stack ($COMPOSE_FILE)"
$DOCKER compose -f "$COMPOSE_FILE" up -d --build

# ------------------------------------------------------------ 5. Health loop --
log "Waiting for $HEALTH_URL (up to $((HEALTH_RETRIES * HEALTH_SLEEP))s)"
for ((i = 1; i <= HEALTH_RETRIES; i++)); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
        log "HEALTHY: $(curl -fsS "$HEALTH_URL")"
        log "Bootstrap complete. Services:"
        $DOCKER compose -f "$COMPOSE_FILE" ps
        exit 0
    fi
    printf '.'
    sleep "$HEALTH_SLEEP"
done

echo
echo "ERROR: $HEALTH_URL never became healthy. Inspect with:" >&2
echo "  $DOCKER compose -f $COMPOSE_FILE ps" >&2
echo "  $DOCKER compose -f $COMPOSE_FILE logs agent-api worker" >&2
exit 1
