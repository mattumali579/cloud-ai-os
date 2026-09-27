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


def _first_str(*names: str, default: str = "") -> str:
    """First env var that is set AND not blank.

    A key left in .env as `HOSTINGER_SMTP_USERNAME=` is a placeholder, not an
    answer. Plain _str() would return that empty string and shadow the fallback,
    which silently left the mailbox unauthenticated.
    """
    for name in names:
        value = os.environ.get(name, "")
        if value.strip():
            return value
    return default


def _first_int(*names: str, default: int) -> int:
    for name in names:
        value = os.environ.get(name, "")
        if value.strip():
            try:
                return int(value.strip())
            except ValueError:
                continue
    return default


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

    # Subscription CLI providers (SUBSCRIPTION_PROVIDERS.md). Metered AI API
    # settings (CF_*, GEMINI_API_KEY, allowlist) were REMOVED by design — no
    # Settings field for a metered AI credential may ever be reintroduced.
    subscription_providers: tuple = ("claude", "codex", "gemini")
    ai_retry_after_quota_minutes: int = 60
    ai_retry_after_auth_minutes: int = 240
    cli_timeout_seconds: int = 300
    claude_cli_bin: str = "claude"
    codex_cli_bin: str = "codex"
    codex_workspace: str = ""
    gemini_cli_bin: str = "gemini"
    claude_cli_model: str = ""  # empty = CLI default

    privacy_config_path: str = str(REPO_ROOT / "config" / "privacy.yaml")
    second_brain_path: str = ""
    second_brain_git_url: str = ""

    discord_bot_token: str = ""
    discord_allowed_guild_id: str = ""
    discord_api_url: str = "http://127.0.0.1:8080"
    discord_owner_ids: str = ""
    discord_default_role: str = "researcher"

    higgsfield_cli_bin: str = "higgsfield"
    higgsfield_allow_generation: bool = False
    higgsfield_timeout_seconds: int = 900

    hostinger_smtp_host: str = "smtp.hostinger.com"
    hostinger_smtp_port: int = 465
    hostinger_smtp_username: str = ""
    hostinger_smtp_password: str = ""
    email_from_name: str = ""
    email_from_address: str = ""
    email_send_enabled: bool = False
    email_outbox_path: str = str(REPO_ROOT / "email_outbox")

    supabase_db_budget_mb: int = 500
    notify_webhook_url: str = ""
    # Autonomous job agent. Personal data and browser state are runtime-only
    # files and are deliberately excluded from Git.
    job_agent_config_path: str = str(REPO_ROOT / "config" / "job_agent.yaml")
    job_profile_path: str = str(REPO_ROOT / "data" / "applicant-profile.yaml")
    job_answers_path: str = str(REPO_ROOT / "data" / "application_answers.yaml")
    job_browser_state_path: str = str(REPO_ROOT / "data" / "browser-state")
    job_browser_executable_path: str = ""
    quick_apply_only: bool = True
    max_application_minutes: int = 8
    max_custom_questions: int = 5
    skip_assessments: bool = True
    skip_long_applications: bool = True
    auto_submit: bool = True
    dry_run: bool = True
    job_min_score: int = 65
    max_applications_per_run: int = 10
    application_timeout_seconds: int = 480
    discovery_http_timeout_seconds: int = 20

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
        subscription_providers=tuple(
            p.strip()
            for p in _str("SUBSCRIPTION_PROVIDERS", "claude,codex,gemini").split(",")
            if p.strip() in ("claude", "codex", "gemini")
        ),
        ai_retry_after_quota_minutes=_int("AI_RETRY_AFTER_QUOTA_MINUTES", 60),
        ai_retry_after_auth_minutes=_int("AI_RETRY_AFTER_AUTH_MINUTES", 240),
        cli_timeout_seconds=_int("CLI_TIMEOUT_SECONDS", 300),
        claude_cli_bin=_str("CLAUDE_CLI_BIN", "claude"),
        codex_cli_bin=_str("CODEX_CLI_BIN", "codex"),
        codex_workspace=_path("CODEX_WORKSPACE", REPO_ROOT),
        gemini_cli_bin=_str("GEMINI_CLI_BIN", "gemini"),
        claude_cli_model=_str("CLAUDE_CLI_MODEL", ""),
        privacy_config_path=_path("PRIVACY_CONFIG_PATH", REPO_ROOT / "config" / "privacy.yaml"),
        second_brain_path=_str("SECOND_BRAIN_PATH"),
        second_brain_git_url=_str("SECOND_BRAIN_GIT_URL"),
        discord_bot_token=_str("DISCORD_BOT_TOKEN"),
        discord_allowed_guild_id=_str("DISCORD_ALLOWED_GUILD_ID"),
        discord_api_url=_str("DISCORD_API_URL", "http://127.0.0.1:8080"),
        discord_owner_ids=_str("DISCORD_OWNER_IDS"),
        discord_default_role=_str("DISCORD_DEFAULT_ROLE", "researcher"),
        higgsfield_cli_bin=_str("HIGGSFIELD_CLI_BIN", "higgsfield"),
        higgsfield_allow_generation=_bool("HIGGSFIELD_ALLOW_GENERATION", False),
        higgsfield_timeout_seconds=_int("HIGGSFIELD_TIMEOUT_SECONDS", 900),
        # Hostinger is the intended mailbox. The generic SMTP_* names are read as a
        # fallback so an already-working mailbox keeps sending while Hostinger
        # credentials are not yet in place.
        hostinger_smtp_host=_first_str("HOSTINGER_SMTP_HOST", "SMTP_HOST", default="smtp.hostinger.com"),
        hostinger_smtp_port=_first_int("HOSTINGER_SMTP_PORT", "SMTP_PORT", default=465),
        hostinger_smtp_username=_first_str("HOSTINGER_SMTP_USERNAME", "SMTP_USER"),
        hostinger_smtp_password=_first_str("HOSTINGER_SMTP_PASSWORD", "SMTP_PASS"),
        email_from_name=_first_str("EMAIL_FROM_NAME", "SMTP_FROM_NAME"),
        email_from_address=_first_str("EMAIL_FROM_ADDRESS", "SMTP_FROM_EMAIL"),
        email_send_enabled=_bool("EMAIL_SEND_ENABLED", False),
        email_outbox_path=_path("EMAIL_OUTBOX_PATH", REPO_ROOT / "email_outbox"),
        supabase_db_budget_mb=_int("SUPABASE_DB_BUDGET_MB", 500),
        notify_webhook_url=_str("NOTIFY_WEBHOOK_URL"),
        job_agent_config_path=_path("JOB_AGENT_CONFIG_PATH", REPO_ROOT / "config" / "job_agent.yaml"),
        job_profile_path=_path("JOB_PROFILE_PATH", REPO_ROOT / "data" / "applicant-profile.yaml"),
        job_answers_path=_path("JOB_ANSWERS_PATH", REPO_ROOT / "data" / "application_answers.yaml"),
        job_browser_state_path=_path("JOB_BROWSER_STATE_PATH", REPO_ROOT / "data" / "browser-state"),
        job_browser_executable_path=_str("JOB_BROWSER_EXECUTABLE_PATH"),
        quick_apply_only=_bool("QUICK_APPLY_ONLY", True),
        max_application_minutes=_int("MAX_APPLICATION_MINUTES", 8),
        max_custom_questions=_int("MAX_CUSTOM_QUESTIONS", 5),
        skip_assessments=_bool("SKIP_ASSESSMENTS", True),
        skip_long_applications=_bool("SKIP_LONG_APPLICATIONS", True),
        auto_submit=_bool("AUTO_SUBMIT", True),
        dry_run=_bool("DRY_RUN", True),
        job_min_score=_int("JOB_MIN_SCORE", 65),
        max_applications_per_run=_int("MAX_APPLICATIONS_PER_RUN", 10),
        application_timeout_seconds=_int("APPLICATION_TIMEOUT_SECONDS", 480),
        discovery_http_timeout_seconds=_int("DISCOVERY_HTTP_TIMEOUT_SECONDS", 20),
    )


def reset_settings_cache() -> None:
    """Test helper: clear the cached Settings after monkeypatching env."""
    get_settings.cache_clear()
