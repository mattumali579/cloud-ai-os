"""Unit tests for cloudos.db — run with NO live database (Agent 4).

Migration ordering/skip/locking logic runs against a fake connection;
configuration behavior is tested by monkeypatching env + reset_settings_cache.
Live round-trips live in tests/test_db_integration.py (skipped without
DATABASE_URL).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cloudos import config, db
from cloudos.contracts import CloudOSError, ErrorCode

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# fakes


class FakeCursor:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """Minimal stand-in for a psycopg connection with dict_row semantics."""

    def __init__(self, applied=(), table_exists=True, fail_on=None):
        self.applied = list(applied)          # versions already in schema_migrations
        self.table_exists = table_exists      # does schema_migrations exist?
        self.fail_on = fail_on                # substring that makes execute() raise
        self.calls = []                       # (sql, params) in execution order
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if self.fail_on is not None and self.fail_on in sql:
            raise RuntimeError("simulated failure")
        if "to_regclass" in sql:
            return FakeCursor([{"reg": "schema_migrations" if self.table_exists else None}])
        if "SELECT version FROM schema_migrations" in sql:
            return FakeCursor([{"version": v} for v in self.applied])
        return FakeCursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


# ---------------------------------------------------------------------------
# fixtures


@pytest.fixture
def set_db_url(monkeypatch):
    """Set DATABASE_URL, reset settings cache + pool; restore both afterwards.

    Setting the env var (even to "") beats load_dotenv(override=False), so a
    developer .env can never leak into these tests.
    """

    def _set(url: str):
        monkeypatch.setenv("DATABASE_URL", url)
        config.reset_settings_cache()
        db.reset_pool()

    yield _set
    config.reset_settings_cache()
    db.reset_pool()


@pytest.fixture
def fake_migrations(tmp_path, monkeypatch):
    """Point db.MIGRATIONS_DIR at a tmp dir; returns an add(name, sql) helper."""
    monkeypatch.setattr(db, "MIGRATIONS_DIR", tmp_path)

    def _add(name: str, sql: str = "SELECT 1"):
        (tmp_path / name).write_text(sql, encoding="utf-8")

    return _add


# ---------------------------------------------------------------------------
# configuration / pool behavior (no DB)


def test_get_pool_unconfigured_raises_dependency_unavailable(set_db_url):
    set_db_url("")
    with pytest.raises(CloudOSError) as excinfo:
        db.get_pool()
    assert excinfo.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE


def test_get_conn_unconfigured_raises_dependency_unavailable(set_db_url):
    set_db_url("")
    with pytest.raises(CloudOSError) as excinfo:
        with db.get_conn():
            pass
    assert excinfo.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE


def test_migrate_unconfigured_raises_dependency_unavailable(set_db_url):
    set_db_url("")
    with pytest.raises(CloudOSError) as excinfo:
        db.migrate()
    assert excinfo.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE


def test_healthcheck_unconfigured_is_false_not_raise(set_db_url):
    set_db_url("")
    assert db.healthcheck() is False


def test_healthcheck_unreachable_is_false_not_raise(set_db_url, monkeypatch):
    monkeypatch.setattr(db, "POOL_TIMEOUT_SECONDS", 2.0)
    set_db_url("postgresql://cloudos:wrong@127.0.0.1:9/cloudos?connect_timeout=1")
    assert db.healthcheck() is False


def test_reset_pool_is_safe_without_a_pool():
    db.reset_pool()
    db.reset_pool()  # idempotent


# ---------------------------------------------------------------------------
# migrate() logic against a fake connection


def test_migrate_applies_in_filename_order(fake_migrations):
    fake_migrations("010_c.sql", "-- m:010_c")
    fake_migrations("001_a.sql", "-- m:001_a")
    fake_migrations("002_b.sql", "-- m:002_b")
    conn = FakeConn()

    applied = db.migrate(conn)

    assert applied == ["001_a", "002_b", "010_c"]
    bodies = [sql for sql, _ in conn.calls if sql.startswith("-- m:")]
    assert bodies == ["-- m:001_a", "-- m:002_b", "-- m:010_c"]
    inserted = [p[0] for sql, p in conn.calls if "INSERT INTO schema_migrations" in sql]
    assert inserted == ["001_a", "002_b", "010_c"]
    assert conn.commits >= 1


def test_migrate_skips_versions_already_recorded(fake_migrations):
    fake_migrations("001_a.sql", "-- m:001_a")
    fake_migrations("002_b.sql", "-- m:002_b")
    conn = FakeConn(applied=["001_a"])

    applied = db.migrate(conn)

    assert applied == ["002_b"]
    bodies = [sql for sql, _ in conn.calls if sql.startswith("-- m:")]
    assert bodies == ["-- m:002_b"]


def test_migrate_creates_schema_migrations_table(fake_migrations):
    fake_migrations("001_a.sql")
    conn = FakeConn(table_exists=False)

    db.migrate(conn)

    assert any("CREATE TABLE IF NOT EXISTS schema_migrations" in sql for sql, _ in conn.calls)


def test_migrate_takes_and_releases_advisory_lock(fake_migrations):
    fake_migrations("001_a.sql", "-- m:001_a")
    conn = FakeConn()

    db.migrate(conn)

    first_sql, first_params = conn.calls[0]
    assert "pg_advisory_lock" in first_sql
    assert first_params == (db.MIGRATION_LOCK_KEY,)
    unlocks = [(sql, p) for sql, p in conn.calls if "pg_advisory_unlock" in sql]
    assert unlocks == [("SELECT pg_advisory_unlock(%s)", (db.MIGRATION_LOCK_KEY,))]
    # unlock happens after every migration body
    assert conn.calls.index(unlocks[0]) > max(
        i for i, (sql, _) in enumerate(conn.calls) if sql.startswith("-- m:")
    )


def test_migrate_rolls_back_and_unlocks_on_failure(fake_migrations):
    fake_migrations("001_a.sql", "-- m:001_a")
    fake_migrations("002_b.sql", "-- m:002_b BOOM")
    conn = FakeConn(fail_on="BOOM")

    with pytest.raises(RuntimeError):
        db.migrate(conn)

    assert conn.rollbacks >= 1
    assert any("pg_advisory_unlock" in sql for sql, _ in conn.calls)
    # nothing recorded for the failed version
    inserted = [p[0] for sql, p in conn.calls if "INSERT INTO schema_migrations" in sql]
    assert inserted == ["001_a"]  # attempted, then rolled back with the batch


def test_migrate_empty_dir_fails_loud(fake_migrations):
    # An empty migrations dir means a broken deployment (wrong CLOUDOS_ROOT /
    # assets not shipped) — migrate must refuse, not silently apply nothing.
    conn = FakeConn()
    with pytest.raises(CloudOSError) as exc:
        db.migrate(conn)
    assert exc.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert "migration" in exc.value.message


def test_pending_migrations_all_pending_when_table_absent(fake_migrations):
    fake_migrations("001_a.sql")
    fake_migrations("002_b.sql")
    conn = FakeConn(table_exists=False)

    assert db.pending_migrations(conn) == ["001_a", "002_b"]
    # read-only: nothing applied, nothing recorded
    assert not any("INSERT INTO schema_migrations" in sql for sql, _ in conn.calls)


def test_pending_migrations_partially_applied(fake_migrations):
    fake_migrations("001_a.sql")
    fake_migrations("002_b.sql")
    conn = FakeConn(applied=["001_a"])

    assert db.pending_migrations(conn) == ["002_b"]


# ---------------------------------------------------------------------------
# real migration files (static contract checks, still no DB)


def test_real_migrations_dir_applies_001_init_first():
    conn = FakeConn(table_exists=False)
    applied = db.migrate(conn)
    assert applied  # at least 001_init
    assert applied[0] == "001_init"
    assert applied == sorted(applied)


def test_001_init_sql_matches_schema_contract():
    sql = (REPO_ROOT / "db" / "migrations" / "001_init.sql").read_text(encoding="utf-8")
    for table in ("jobs", "agent_runs", "events", "notifications", "quota_usage", "sb_index"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, table
    assert "CHECK (cost_usd = 0)" in sql              # paid AI permanently disabled
    assert "(status, run_at, priority)" in sql        # required jobs index
    assert "gen_random_uuid()" in sql
    assert "PRIMARY KEY (provider, day)" in sql       # quota_usage composite pk
