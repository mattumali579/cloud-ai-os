from pathlib import Path

from cloudos.config import reset_settings_cache
from cloudos.router.providers import base, codex_cli


def test_codex_search_runs_in_configured_workspace(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setenv("CODEX_WORKSPACE", str(tmp_path))
    reset_settings_cache()
    monkeypatch.setattr(base, "which", lambda binary: "/bin/codex")
    monkeypatch.setattr(codex_cli, "_auth_json", lambda: tmp_path / "missing-auth.json")

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[-2:] == ["login", "status"]:
            return base.CliResult(0, "Logged in using ChatGPT", "")
        return base.CliResult(0, "researched answer", "")

    monkeypatch.setattr(base, "RUNNER", runner)
    result = codex_cli.generate("research this", web_search=True)

    assert result.text == "researched answer"
    argv, kwargs = calls[-1]
    assert argv[:3] == ["/bin/codex", "--search", "exec"]
    assert kwargs["cwd"] == str(tmp_path)
    reset_settings_cache()
