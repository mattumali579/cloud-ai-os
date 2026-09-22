from pathlib import Path

import pytest

from cloudos import employees
from cloudos.config import reset_settings_cache
from cloudos.contracts import PrivacyLabel, RouteResult


def test_channel_aliases_resolve():
    assert employees.role_for_channel("researcher") == "researcher"
    assert employees.role_for_channel("content-strategy") == "content-strategist"
    assert employees.role_for_channel("random") is None


def test_prompt_loads_existing_employee_and_second_brain(monkeypatch, tmp_path: Path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "researcher.md").write_text("USE THIS RESEARCH METHOD", encoding="utf-8")
    monkeypatch.setenv("SECOND_BRAIN_PATH", str(tmp_path))
    reset_settings_cache()
    monkeypatch.setattr(employees, "_context_for", lambda message: ("KNOWN BRAND FACT", ["brand.md"]))

    prompt, files = employees.build_employee_prompt("researcher", "Find current competitors")

    assert "USE THIS RESEARCH METHOD" in prompt
    assert "KNOWN BRAND FACT" in prompt
    assert "cite direct source URLs" in prompt
    assert files == ["brand.md"]
    reset_settings_cache()


def test_invoke_forces_codex_and_enables_search(monkeypatch):
    captured = {}

    def fake_route(req):
        captured["req"] = req
        return RouteResult(ok=True, text="done", model="codex_cli")

    monkeypatch.setattr("cloudos.router.route", fake_route)
    monkeypatch.setattr(employees, "build_employee_prompt", lambda *args, **kwargs: ("prompt", []))

    result, _ = employees.invoke_employee(
        "researcher", "topic", privacy_label=PrivacyLabel.INTERNAL
    )

    assert result.text == "done"
    assert captured["req"].model_hint == "codex"
    assert captured["req"].meta == {"web_search": True}


def test_unknown_employee_is_rejected():
    with pytest.raises(ValueError):
        employees.normalize_role("wizard")
