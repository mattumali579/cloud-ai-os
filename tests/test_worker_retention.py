"""Retention pruner tests (Agent 3) — fake connections, configured windows."""
from __future__ import annotations

import json

import pytest

from cloudos.config import reset_settings_cache
from cloudos.worker import retention

from test_worker_fakes import FakeConn, executed_sql

# Deliberately NOT the defaults (14/7/7/30) and not 7 across the board, so a
# hardcoded value anywhere fails these tests.
DAYS = {
    "AGENT_RUN_RETENTION_DAYS": "21",
    "EVENT_RETENTION_DAYS": "3",
    "COMPLETED_JOB_RETENTION_DAYS": "9",
    "FAILED_JOB_RETENTION_DAYS": "45",
}

COUNTS = [{"n": 5}, {"n": 2}, {"n": 7}, {"n": 11}, {"n": 1}]  # per target, in order
TABLES = ["jobs_completed", "jobs_failed", "agent_runs", "events", "notifications"]


@pytest.fixture
def retention_env(monkeypatch):
    for key, value in DAYS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("RETENTION_DRY_RUN", "false")
    reset_settings_cache()
    yield
    reset_settings_cache()


def _count_params(conn):
    return [params for sql, params in conn.executed if sql.startswith("SELECT count(*)")]


# -- dry-run: selects but never deletes ----------------------------------

def test_dry_run_counts_but_deletes_nothing(retention_env):
    conn = FakeConn(results=list(COUNTS) + [None])  # 5 counts + metrics event insert
    report = retention.prune(dry_run=True, conn=conn)
    assert [report[t]["selected"] for t in TABLES] == [5, 2, 7, 11, 1]
    assert all(report[t]["deleted"] == 0 for t in TABLES)
    assert all(report[t]["failures"] == 0 for t in TABLES)
    assert not any(sql.startswith("DELETE") for sql in executed_sql(conn))


def test_dry_run_default_comes_from_settings(retention_env, monkeypatch):
    monkeypatch.setenv("RETENTION_DRY_RUN", "true")
    reset_settings_cache()
    conn = FakeConn(results=list(COUNTS) + [None])
    retention.prune(conn=conn)  # dry_run omitted → settings.retention_dry_run
    assert not any(sql.startswith("DELETE") for sql in executed_sql(conn))


# -- live run -------------------------------------------------------------

def test_live_run_deletes_and_reports_rowcounts(retention_env):
    conn = FakeConn(results=list(COUNTS) + [None] + [5, 2, 7, 11, 1])
    report = retention.prune(dry_run=False, conn=conn)
    assert [report[t]["deleted"] for t in TABLES] == [5, 2, 7, 11, 1]
    deletes = [sql for sql in executed_sql(conn) if sql.startswith("DELETE")]
    assert len(deletes) == 5


def test_metrics_event_emitted_before_any_delete(retention_env):
    conn = FakeConn(results=list(COUNTS) + [None] + [5, 2, 7, 11, 1])
    retention.prune(dry_run=False, conn=conn)
    sqls = executed_sql(conn)
    event_idx = next(i for i, s in enumerate(sqls) if "INSERT INTO events" in s)
    first_delete_idx = next(i for i, s in enumerate(sqls) if s.startswith("DELETE"))
    assert event_idx < first_delete_idx
    _, params = conn.executed[event_idx]
    assert params[2] == "RETENTION_METRICS"
    meta = json.loads(params[4])
    assert meta["selected"]["events"] == 11
    assert meta["dry_run"] is False
    assert meta["retention_days"] == {
        "agent_runs": 21, "events": 3, "completed_jobs": 9, "failed_jobs": 45
    }


# -- configured (not hardcoded) windows ----------------------------------

def test_uses_configured_day_values_not_hardcoded(retention_env):
    conn = FakeConn(results=list(COUNTS) + [None] + [0, 0, 0, 0, 0])
    retention.prune(dry_run=False, conn=conn)
    # counts and deletes both parametrized with the configured windows, in order
    expected = [(9,), (45,), (21,), (3,), (3,)]
    assert _count_params(conn) == expected
    delete_params = [params for sql, params in conn.executed if sql.startswith("DELETE")]
    assert delete_params == expected
    # nothing fell back to the classic hardcoded 7
    assert (7,) not in _count_params(conn)


def test_respects_a_different_configuration(monkeypatch):
    monkeypatch.setenv("AGENT_RUN_RETENTION_DAYS", "2")
    monkeypatch.setenv("EVENT_RETENTION_DAYS", "60")
    monkeypatch.setenv("COMPLETED_JOB_RETENTION_DAYS", "1")
    monkeypatch.setenv("FAILED_JOB_RETENTION_DAYS", "365")
    reset_settings_cache()
    try:
        conn = FakeConn(results=list(COUNTS) + [None])
        retention.prune(dry_run=True, conn=conn)
        assert _count_params(conn) == [(1,), (365,), (2,), (60,), (60,)]
    finally:
        reset_settings_cache()


# -- safety: never touches live jobs -------------------------------------

def test_job_deletes_only_target_terminal_statuses(retention_env):
    conn = FakeConn(results=list(COUNTS) + [None] + [0, 0, 0, 0, 0])
    retention.prune(dry_run=False, conn=conn)
    job_deletes = [sql for sql in executed_sql(conn)
                   if sql.startswith("DELETE FROM jobs")]
    assert len(job_deletes) == 2
    completed, failed = job_deletes
    assert "status IN ('succeeded','cancelled')" in completed
    assert "status = 'failed'" in failed
    for sql in job_deletes:
        assert "'queued'" not in sql
        assert "'running'" not in sql
        assert "'blocked'" not in sql


def test_notifications_delete_requires_delivered(retention_env):
    conn = FakeConn(results=list(COUNTS) + [None] + [0, 0, 0, 0, 0])
    retention.prune(dry_run=False, conn=conn)
    notif = next(sql for sql in executed_sql(conn)
                 if sql.startswith("DELETE FROM notifications"))
    assert "delivered = true" in notif


# -- fault isolation ------------------------------------------------------

def test_one_table_failure_is_counted_and_does_not_abort(retention_env):
    conn = FakeConn(results=list(COUNTS) + [None] + [5, 2])
    conn.raise_on = ("DELETE FROM agent_runs", RuntimeError("boom"))
    report = retention.prune(dry_run=False, conn=conn)
    assert report["agent_runs"]["failures"] == 1
    assert report["agent_runs"]["deleted"] == 0
    assert report["jobs_completed"]["deleted"] == 5
    # later tables still attempted after the failure
    assert any(sql.startswith("DELETE FROM events") for sql in executed_sql(conn))
    assert conn.rollbacks >= 1
