"""Central configuration. Owned by the lead (Agent 1).

The ONLY module that reads os.environ. Everything else uses get_settings().
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

def _find_repo_root() -> Path:
    """Locate the directory holding db/migrations and config/.

    Source checkout: src/cloudos/config.py → parents[2] is the repo root.
    Installed (Docker: cloudos in site-packages, assets copied to /app):
    CLOUDOS_ROOT env wins, else fall back to a CWD that has db/migrations.
    """
    env = os.environ.get("CLOUDOS_ROOT")
    if env:
        return Path(env).resolve()
    src_root = Path(__file__).resolve().parents[2]
    if (src_root / "db" / "migrations").is_dir():
        return src_root
    cwd = Path.cwd()
    if (cwd / "db" / "migrations").is_dir():
        return cwd
    return src_root


REPO_ROOT = _find_repo_root()


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    v = os.environ.get(name)
    try:
        return int(v) if v is not None and v.strip() != "" else default
    except ValueError:
        return default


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _path(name: str, default: Path) -> str:
    """Path-valued env var; relative values resolve against REPO_ROOT, not CWD."""
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return str(default)
    p = Path(v.strip())
    return str(p if p.is_absolute() else REPO_ROOT / p)


@dataclass(frozen=True)
class Settings:
    environment: str = "local"
    instance_name: str = "cloudos-local"

    database_url: str = ""

    agent_api_token: str = ""
    agent_api_port: int = 8080

    worker_poll_interval_seconds: int = 5
    worker_concurrency: int = 2

    agent_run_retention_days: int = 14
    event_retention_days: int = 7
    completed_job_retention_days: int = 7
    failed_job_retention_days: int = 30
    retention_dry_run: bool = False

    # Zero-cost enforcement. These are defaults AND invariants: the router treats
    # paid AI as disabled regardless, but keep the env contract explicit.
    allow_paid_ai: bool = False
    allow_paid_infrastructure: bool = False
    max_daily_paid_ai_usd: float = 0.0
    max_monthly_paid_ai_usd: float = 0.0

    free_model_allowlist_path: str = str(REPO_ROOT / "config" / "free_model_allowlist.json")
    cf_account_id: str = ""
    cf_workers_ai_token: str = ""
    workers_ai_daily_budget: int = 9000
    gemini_api_key: str = ""
    gemini_daily_request_budget: int = 200

    privacy_config_path: str = str(REPO_ROOT / "config" / "privacy.yaml")
    second_brain_path: str = ""
    second_brain_git_url: str = ""

    supabase_db_budget_mb: int = 500
    notify_webhook_url: str = ""

    extra: dict = field(default_factory=dict)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env", override=False)
    return Settings(
        environment=_str("ENVIRONMENT", "local"),
        instance_name=_str("INSTANCE_NAME", "cloudos-local"),
        database_url=_str("DATABASE_URL"),
        agent_api_token=_str("AGENT_API_TOKEN"),
        agent_api_port=_int("AGENT_API_PORT", 8080),
        worker_poll_interval_seconds=_int("WORKER_POLL_INTERVAL_SECONDS", 5),
        worker_concurrency=_int("WORKER_CONCURRENCY", 2),
        agent_run_retention_days=_int("AGENT_RUN_RETENTION_DAYS", 14),
        event_retention_days=_int("EVENT_RETENTION_DAYS", 7),
        completed_job_retention_days=_int("COMPLETED_JOB_RETENTION_DAYS", 7),
        failed_job_retention_days=_int("FAILED_JOB_RETENTION_DAYS", 30),
        retention_dry_run=_bool("RETENTION_DRY_RUN", False),
        allow_paid_ai=_bool("ALLOW_PAID_AI", False),
        allow_paid_infrastructure=_bool("ALLOW_PAID_INFRASTRUCTURE", False),
        max_daily_paid_ai_usd=0.0,
        max_monthly_paid_ai_usd=0.0,
        free_model_allowlist_path=_path(
            "FREE_MODEL_ALLOWLIST_PATH", REPO_ROOT / "config" / "free_model_allowlist.json"
        ),
        cf_account_id=_str("CF_ACCOUNT_ID"),
        cf_workers_ai_token=_str("CF_WORKERS_AI_TOKEN"),
        workers_ai_daily_budget=_int("WORKERS_AI_DAILY_BUDGET", 9000),
        gemini_api_key=_str("GEMINI_API_KEY"),
        gemini_daily_request_budget=_int("GEMINI_DAILY_REQUEST_BUDGET", 200),
        privacy_config_path=_path("PRIVACY_CONFIG_PATH", REPO_ROOT / "config" / "privacy.yaml"),
        second_brain_path=_str("SECOND_BRAIN_PATH"),
        second_brain_git_url=_str("SECOND_BRAIN_GIT_URL"),
        supabase_db_budget_mb=_int("SUPABASE_DB_BUDGET_MB", 500),
        notify_webhook_url=_str("NOTIFY_WEBHOOK_URL"),
    )


def reset_settings_cache() -> None:
    """Test helper: clear the cached Settings after monkeypatching env."""
    get_settings.cache_clear()
