"""Regression checks for the native Windows worker bootstrap."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


def test_native_worker_does_not_override_database_url() -> None:
    """The bootstrap must allow cloudos.config to load DATABASE_URL from .env."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "host_worker_boot.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    env_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ENV" for target in node.targets)
    )
    assert isinstance(env_assignment.value, ast.Dict)
    keys = {key.value for key in env_assignment.value.keys if isinstance(key, ast.Constant)}
    assert "DATABASE_URL" not in keys


def test_native_worker_starts_api_only_when_loopback_api_is_absent(monkeypatch) -> None:
    path = Path(__file__).resolve().parents[1] / "scripts" / "host_worker_boot.py"
    spec = importlib.util.spec_from_file_location("host_worker_boot_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calls = []
    monkeypatch.setattr(module, "api_is_running", lambda port: False)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    module.start_api_if_needed(8080)

    assert len(calls) == 1
    assert calls[0][0][0][1].endswith("scripts\\host_api_boot.py")
    assert calls[0][1]["cwd"] == module.REPO
