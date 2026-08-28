"""Database access for Cloud AI OS. Owned by Agent 4.

Interface pinned in contracts/ARCHITECTURE.md SS5 -- consumers (api, worker,
router, second_brain) code against EXACTLY:

    get_pool() -> psycopg_pool.ConnectionPool
    get_conn()                      # contextmanager yielding a pooled connection
    migrate(conn=None) -> list[str]
    healthcheck() -> bool
    reset_pool() -> None

Rows come back as dicts (psycopg.rows.dict_row). Connections are created with
prepare_threshold=None so the pool is safe behind a transaction-mode pooler
(Supabase port 6543). Migrations use a session-level advisory lock and must
therefore run over a SESSION-mode connection -- see docs/SUPABASE.md.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import psycopg_pool
from psycopg.rows import dict_row

from cloudos.config import REPO_ROOT, get_settings
from cloudos.contracts import CloudOSError, ErrorCode

MIGRATIONS_DIR: Path = REPO_ROOT / "db" / "migrations"

# Session-level advisory lock guarding the migration runner across processes
# (api + worker both call migrate() at startup). 0x636C6F75646F7321 is the
# bytes b"cloudos!" -- stable, and fits in a signed bigint.
MIGRATION_LOCK_KEY = 0x636C6F75646F7321

# Supabase free tier allows few concurrent connections -- keep the pool small.
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 4
POOL_TIMEOUT_SECONDS = 30.0

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""

_pool: Optional[psycopg_pool.ConnectionPool] = None
_pool_lock = threading.Lock()


def get_pool() -> psycopg_pool.ConnectionPool:
    """Lazy singleton ConnectionPool built from settings.database_url.

    Raises CloudOSError(DEPENDENCY_UNAVAILABLE) if DATABASE_URL is unset.
    """
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            settings = get_settings()
            if not settings.database_url:
                raise CloudOSError(
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "DATABASE_URL is not configured; database access is unavailable",
                    {"setting": "database_url"},
                )
            _pool = psycopg_pool.ConnectionPool(
                settings.database_url,
                min_size=POOL_MIN_SIZE,
                max_size=POOL_MAX_SIZE,
                timeout=POOL_TIMEOUT_SECONDS,
                kwargs={"row_factory": dict_row, "prepare_threshold": None},
                name="cloudos",
                open=True,
            )
        return _pool


@contextmanager
def get_conn():
    """Context manager yielding a pooled psycopg connection (dict rows).

    Commits on clean exit, rolls back on exception (psycopg_pool semantics).
    """
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


def migrate(conn=None) -> list[str]:
    """Apply db/migrations/*.sql in filename order; return list of versions applied.

    - Serialized via pg_advisory_lock(MIGRATION_LOCK_KEY) (session-level).
    - Idempotent: versions already recorded in schema_migrations are skipped.
    - Creates schema_migrations itself if absent.
    - All pending migrations + their bookkeeping commit atomically; any failure
      rolls back the whole batch and re-raises.

    Pass an explicit connection (tests, one-off scripts) or omit it to use the
    pool (raises CloudOSError(DEPENDENCY_UNAVAILABLE) if DATABASE_URL unset).
    """
    if conn is not None:
        return _migrate(conn)
    with get_conn() as pooled:
        return _migrate(pooled)


def healthcheck() -> bool:
    """SELECT 1 round-trip. Returns False on ANY failure -- never raises."""
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT 1 AS ok")
            return _row_value(cur.fetchone(), "ok") == 1
    except Exception:
        return False


def reset_pool() -> None:
    """Test helper: close and discard the singleton pool (safe if none exists)."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.close()
            except Exception:
                pass
            _pool = None


def pending_migrations(conn=None) -> list[str]:
    """Versions in db/migrations/ not yet recorded in schema_migrations.

    Additive helper (used by scripts/apply_migrations.py --dry-run); the SS5
    pinned interface is unchanged. Read-only: applies nothing.
    """
    if conn is not None:
        return _pending(conn)
    with get_conn() as pooled:
        return _pending(pooled)


# ---------------------------------------------------------------------------
# internals


def _migration_files() -> list[Path]:
    files = sorted((p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file()), key=lambda p: p.name)
    if not files:
        # An empty migrations dir means a broken deployment (wrong CLOUDOS_ROOT /
        # assets not shipped), never a valid state — refuse rather than silently
        # "migrating" nothing and failing later with UndefinedTable.
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "no migration files found",
            {"migrations_dir": str(MIGRATIONS_DIR)},
        )
    return files


def _row_value(row, key):
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    return row[0]


def _applied_versions(conn) -> set:
    cur = conn.execute("SELECT to_regclass('schema_migrations') AS reg")
    if not _row_value(cur.fetchone(), "reg"):
        return set()
    cur = conn.execute("SELECT version FROM schema_migrations")
    return {_row_value(row, "version") for row in cur.fetchall()}


def _pending(conn) -> list[str]:
    done = _applied_versions(conn)
    return [p.stem for p in _migration_files() if p.stem not in done]


def _migrate(conn) -> list[str]:
    applied: list[str] = []
    conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
    try:
        conn.execute(_SCHEMA_MIGRATIONS_DDL)
        done = _applied_versions(conn)
        for path in _migration_files():
            version = path.stem
            if version in done:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied.append(version)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        # Advisory locks are session-level: a rollback does NOT release them.
        try:
            conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
            conn.commit()
        except Exception:
            pass
    return applied
