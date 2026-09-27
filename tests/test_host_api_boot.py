"""Regression checks for the native Windows API bootstrap."""
from __future__ import annotations

import ast
from pathlib import Path


def test_native_api_bootstrap_binds_loopback_only() -> None:
    path = Path(__file__).resolve().parents[1] / "scripts" / "host_api_boot.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "uvicorn"
        and node.func.attr == "run"
    ]
    assert len(calls) == 1
    host = next(keyword.value for keyword in calls[0].keywords if keyword.arg == "host")
    assert isinstance(host, ast.Constant)
    assert host.value == "127.0.0.1"
