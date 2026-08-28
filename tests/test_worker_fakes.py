"""Shared fakes for the worker test suite (Agent 3). No live DB, no network.

Named test_worker_fakes.py so it stays inside Agent 3's tests/test_worker*.py
ownership; it contains no tests itself.
"""
from __future__ import annotations

import contextlib
import re
import sys
import types
from typing import Any, Callable, Optional


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


class FakeCursor:
    def __init__(self, conn: "FakeConn"):
        self.conn = conn
        self._rows: list = []
        self.rowcount = -1

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        sql_n = _norm(sql)
        self.conn.executed.append((sql_n, tuple(params) if params is not None else None))
        result = self.conn._next_result(sql_n, params)
        if isinstance(result, int):
            self.rowcount = result
            self._rows = []
        elif result is None:
            self.rowcount = 0
            self._rows = []
        elif isinstance(result, list):
            self._rows = list(result)
            self.rowcount = len(self._rows)
        else:
            self._rows = [result]
            self.rowcount = 1

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Scripted psycopg-like connection.

    `results`: queue of scripted results, consumed one per execute().
      dict → single row; list → many rows; int → rowcount (e.g. DELETE);
      None → empty result.
    `result_fn(sql, params)`: dynamic alternative to the queue.
    `raise_on`: (sql_substring, exception) → execute() raises when matched.
    """

    def __init__(self, results: Optional[list] = None,
                 result_fn: Optional[Callable[[str, Any], Any]] = None):
        self.executed: list[tuple[str, Optional[tuple]]] = []
        self.commits = 0
        self.rollbacks = 0
        self.results = list(results or [])
        self.result_fn = result_fn
        self.raise_on: Optional[tuple[str, Exception]] = None

    def cursor(self, *a: Any, **k: Any) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def _next_result(self, sql: str, params: Any):
        if self.raise_on and self.raise_on[0] in sql:
            raise self.raise_on[1]
        if self.result_fn is not None:
            return self.result_fn(sql, params)
        if self.results:
            return self.results.pop(0)
        return None


def executed_sql(conn: FakeConn) -> list[str]:
    return [sql for sql, _ in conn.executed]


def job_row(**over: Any) -> dict:
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "type": "noop",
        "status": "queued",
        "priority": 100,
        "payload": {},
        "result": None,
        "error_code": None,
        "error": None,
        "attempts": 0,
        "max_attempts": 3,
        "run_at": None,
        "created_at": None,
        "started_at": None,
        "finished_at": None,
        "locked_by": None,
        "locked_at": None,
    }
    row.update(over)
    return row


def install_fake_db(monkeypatch, conn: Any, migrate_exc: Optional[Exception] = None) -> types.ModuleType:
    """Install a fake `cloudos.db` module (Agent 4's, absent while built in parallel)."""
    import cloudos

    fake = types.ModuleType("cloudos.db")
    calls = {"migrate": 0}

    def migrate(conn_arg: Any = None) -> list[str]:
        calls["migrate"] += 1
        if migrate_exc is not None:
            raise migrate_exc
        return []

    @contextlib.contextmanager
    def get_conn():
        yield conn

    fake.migrate = migrate
    fake.get_conn = get_conn
    fake.healthcheck = lambda: True
    fake.calls = calls
    monkeypatch.setitem(sys.modules, "cloudos.db", fake)
    # `from cloudos import db` style resolution falls back to the package attr;
    # patch it too so no stale module leaks between tests.
    monkeypatch.setattr(cloudos, "db", fake, raising=False)
    return fake


def install_fake_module(monkeypatch, name: str, **attrs: Any) -> types.ModuleType:
    """Install an arbitrary fake module (e.g. cloudos.router, cloudos.second_brain)."""
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    monkeypatch.setitem(sys.modules, name, mod)
    if "." in name:
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            monkeypatch.setattr(parent, child, mod, raising=False)
    return mod
