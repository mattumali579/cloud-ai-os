"""Regression checks for the Mini PC host-worker supervisor."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PATH = REPO / "scripts" / "host_worker_supervisor.py"


def _load():
    spec = importlib.util.spec_from_file_location("host_worker_supervisor_test", PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_supervisor_does_not_spawn_host_api() -> None:
    tree = ast.parse(PATH.read_text(encoding="utf-8"))
    text = ast.dump(tree)
    assert "host_api_boot" not in text
    assert "Popen" not in text


def test_second_acquire_lock_fails_while_first_held(tmp_path) -> None:
    module = _load()
    lock = tmp_path / "host_worker.pid"
    assert module.acquire_lock(lock) is True
    other = importlib.util.spec_from_file_location("host_worker_supervisor_other", PATH)
    assert other and other.loader
    other_mod = importlib.util.module_from_spec(other)
    other.loader.exec_module(other_mod)
    assert other_mod.acquire_lock(lock) is False
    module._lock_fh.close()
    module._lock_fh = None


def test_wait_for_api_succeeds_when_healthz_reports_db(monkeypatch) -> None:
    module = _load()
    monkeypatch.setattr(module, "api_health", lambda port, timeout=2.0: {"status": "ok", "db": True})
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_k: None)
    assert module.wait_for_api(8080, stop_after=1) is True


def test_wait_for_api_times_out_when_db_false(monkeypatch) -> None:
    module = _load()
    monkeypatch.setattr(module, "api_health", lambda port, timeout=2.0: {"status": "ok", "db": False})
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    assert module.wait_for_api(8080, stop_after=0) is False
