"""scripts/claude_task.py - the Hermes -> Claude Code handoff. No real Claude call here."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cloudos.router.providers import base

SPEC = importlib.util.spec_from_file_location("claude_task", Path(__file__).resolve().parent.parent / "scripts" / "claude_task.py")
claude_task = importlib.util.module_from_spec(SPEC)
sys.modules["claude_task"] = claude_task
SPEC.loader.exec_module(claude_task)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    monkeypatch.setattr(base, "which", lambda b: "claude")
    return tmp_path


def fake_runner(stdout, returncode=0, timed_out=False, edit=None):
    calls = []

    def runner(argv, *, timeout, cwd, **kw):
        calls.append({"argv": argv, "timeout": timeout, "cwd": cwd})
        if edit:
            (Path(cwd) / edit).write_text("y = 2\n")
        return base.CliResult(returncode, stdout, "", timed_out=timed_out)

    runner.calls = calls
    return runner


def test_argv_is_bounded_and_cannot_ship():
    argv = claude_task.build_argv("do it", 7)
    assert argv[:2] == ["claude", "-p"] and argv[2].endswith("TASK:\ndo it")
    assert argv[argv.index("--max-turns") + 1] == "7"
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    allowed = argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
    denied = argv[argv.index("--disallowedTools") + 1:]
    assert "Edit" in allowed and "Bash(python -m pytest:*)" in allowed
    assert not any("push" in t or "outreach_sender" in t or t == "Bash" for t in allowed)
    assert {"Bash(git push:*)", "Bash(python outreach_sender.py:*)", "CronCreate"} <= set(denied)


def test_success_reports_files_from_git_not_from_prose(repo):
    out = json.dumps({"result": "done, tests pass", "num_turns": 3, "is_error": False, "subtype": "success"})
    runner = fake_runner(out, edit="b.py")
    rep = claude_task.run("add b", cwd=repo, runner=runner, max_turns=5, timeout=60)
    assert rep["ok"] is True and rep["turns"] == 3
    assert rep["files_changed"] == ["b.py"]            # a.py was already untracked before: not reported
    assert runner.calls[0]["timeout"] == 60 and runner.calls[0]["cwd"] == str(repo)
    assert (repo / rep["log"]).is_file()


def test_no_edit_means_no_files_changed_even_if_claude_claims_it(repo):
    out = json.dumps({"result": "I changed a.py", "num_turns": 1, "is_error": False})
    rep = claude_task.run("x", cwd=repo, runner=fake_runner(out))
    assert rep["ok"] is True and rep["files_changed"] == []


@pytest.mark.parametrize("stdout,rc,timed_out,needle", [
    ("", -1, True, "timed out"),
    (json.dumps({"result": "Reached max turns", "is_error": True, "subtype": "error_max_turns"}), 1, False, "max turns"),
    ("not json", 0, False, "exit 0"),
])
def test_failures_are_not_ok(repo, stdout, rc, timed_out, needle):
    rep = claude_task.run("x", cwd=repo, runner=fake_runner(stdout, rc, timed_out))
    assert rep["ok"] is False and needle in rep["error"].lower()


def test_missing_claude_binary(repo, monkeypatch):
    monkeypatch.setattr(base, "which", lambda b: None)
    rep = claude_task.run("x", cwd=repo, runner=fake_runner("{}"))
    assert rep["ok"] is False and "not installed" in rep["error"]


def test_metered_keys_never_reach_claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-would-cost-money")
    assert "ANTHROPIC_API_KEY" not in base.scrubbed_env()
    assert claude_task.base.run_cli is base.run_cli     # the handoff uses the scrubbing runner


def test_empty_task_is_rejected():
    with pytest.raises(SystemExit):
        claude_task.main(["   "])
