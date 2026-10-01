"""scripts/claude_task.py - the planner -> Claude Code handoff. No real Claude call here."""
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

CLAIMS = {"what_changed": "added b.py", "files_changed": ["b.py"], "commands_run": ["python -m pytest -q"],
          "actual_results": "1 passed", "blockers": [], "unverified": []}


def spec(**over):
    s = {"key": "add-b", "objective": "Add b.py", "files": ["b.py"], "constraints": ["Do not touch a.py"],
         "success_test": "python -m pytest tests/test_b.py -q", "evidence": ["test output"]}
    s.update(over)
    return s


def answer(claims=CLAIMS, **over):
    return json.dumps({"result": "done", "structured_output": claims, "num_turns": 3, "is_error": False,
                       "subtype": "success", **over})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    monkeypatch.setattr(base, "which", lambda b: "claude")
    return tmp_path


def fake_runner(stdout, returncode=0, timed_out=False, edit=None, test_rc=0, test_out="1 passed"):
    """Answers the claude call with `stdout` and the success-test call with `test_rc`/`test_out`."""
    calls = []

    def runner(argv, *, timeout, cwd, **kw):
        calls.append({"argv": argv, "timeout": timeout, "cwd": cwd, **kw})
        if argv[0] != "claude":
            return base.CliResult(test_rc, test_out, "")
        if edit:
            (Path(cwd) / edit).write_text("y = 2\n")
        return base.CliResult(returncode, stdout, "", timed_out=timed_out)

    runner.calls = calls
    return runner


def test_argv_is_bounded_and_cannot_ship():
    argv = claude_task.build_argv(spec(), 7)
    assert argv[:2] == ["claude", "-p"]
    for part in ("OBJECTIVE:\nAdd b.py", "RELEVANT FILES:\n- b.py", "CONSTRAINTS:\n- Do not touch a.py",
                 "SUCCESS TEST (must exit 0):\npython -m pytest tests/test_b.py -q", "REQUIRED EVIDENCE:\n- test output"):
        assert part in argv[2]
    assert "You do not choose work" in argv[2] and "do not propose next tasks" in argv[2]
    assert argv[argv.index("--max-turns") + 1] == "7"
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert set(json.loads(argv[argv.index("--json-schema") + 1])["required"]) == {
        "what_changed", "files_changed", "commands_run", "actual_results", "blockers", "unverified"}
    allowed = argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
    denied = argv[argv.index("--disallowedTools") + 1:]
    assert "Edit" in allowed and "Bash(python -m pytest:*)" in allowed
    assert not any("push" in t or "outreach_sender" in t or t == "Bash" for t in allowed)
    assert {"Bash(git push:*)", "Bash(python outreach_sender.py:*)", "CronCreate", "Agent"} <= set(denied)


@pytest.mark.parametrize("field", ["key", "objective", "files", "constraints", "success_test", "evidence"])
def test_a_spec_missing_any_field_is_refused_and_nothing_runs(repo, field):
    for bad in ({k: v for k, v in spec().items() if k != field}, spec(**{field: "" if field in claude_task.SPEC_TEXT else []})):
        runner = fake_runner(answer())
        rep = claude_task.run(bad, cwd=repo, runner=runner)
        assert rep["status"] == "invalid_spec" and rep["verified"] is False
        assert any(e.startswith(field + ":") for e in rep["errors"]) and runner.calls == []


@pytest.mark.parametrize("cmd,ok", [
    ("python -m pytest tests/test_b.py -q", True), ("python -m ruff check src", True),
    ("python outreach_sender.py status", True), ("python outreach_status.py", True),
    ("python outreach_sender.py send --live", False), ("python outreach_sender.py", False),
    ("git push", False), ("python -c 'import os'", False), ("pytest -q", False), ("python 'unclosed", False),
])
def test_success_test_must_be_a_read_only_check(cmd, ok):
    assert (claude_task.validate_spec(spec(success_test=cmd)) == []) is ok


def test_verified_needs_the_rerun_test_to_pass_and_reports_git_not_prose(repo, monkeypatch):
    monkeypatch.setenv("BRIGHTREACH_PYTHON", "/opt/proj/bin/python")
    runner = fake_runner(answer(), edit="b.py", test_out="\x1b[32m1 passed\x1b[0m")
    rep = claude_task.run(spec(), cwd=repo, runner=runner, max_turns=5, timeout=60, test_timeout=30)
    assert rep["status"] == "verified" and rep["verified"] is True and rep["turns"] == 3
    assert rep["evidence"]["files_changed"] == ["b.py"]     # a.py was already untracked before: not reported
    assert rep["evidence"]["changed_outside_task_files"] == []
    assert rep["evidence"]["success_test"]["exit_code"] == 0 and rep["evidence"]["success_test"]["output_tail"] == "1 passed"
    assert rep["worker_claims"] == CLAIMS
    claude_call, test_call = runner.calls
    assert claude_call["timeout"] == 60 and claude_call["cwd"] == str(repo)
    assert test_call["argv"] == ["/opt/proj/bin/python", "-m", "pytest", "tests/test_b.py", "-q"] and test_call["timeout"] == 30
    assert (repo / rep["log"]).is_file()
    assert claude_task.read_ledger(repo)[-1]["status"] == "verified"


def test_claude_saying_it_passed_does_not_count_when_the_rerun_fails(repo):
    rep = claude_task.run(spec(), cwd=repo, runner=fake_runner(answer(), edit="b.py", test_rc=1, test_out="1 failed"))
    assert rep["status"] == "failed_test" and rep["verified"] is False and rep["ok"] is True
    assert rep["evidence"]["success_test"]["passed"] is False and "1 failed" in rep["evidence"]["success_test"]["output_tail"]


def test_claims_are_checked_against_git(repo):
    claims = {**CLAIMS, "files_changed": ["a.py"]}
    rep = claude_task.run(spec(files=["a.py"]), cwd=repo, runner=fake_runner(answer(claims), edit="b.py"))
    assert rep["evidence"]["claimed_but_not_changed"] == ["a.py"]
    assert rep["evidence"]["changed_but_not_claimed"] == ["b.py"]
    assert rep["evidence"]["changed_outside_task_files"] == ["b.py"]


def test_prose_instead_of_the_required_form_is_not_verified(repo):
    out = json.dumps({"result": "all done, trust me", "num_turns": 1, "is_error": False})
    rep = claude_task.run(spec(), cwd=repo, runner=fake_runner(out))
    assert rep["status"] == "bad_answer" and rep["verified"] is False and rep["worker_claims"] is None


def test_blocked_is_recorded_and_not_retried_blindly(repo):
    blocked = answer({**CLAIMS, "blockers": ["AGENTMAIL_API_KEY is not set locally"]})
    rep = claude_task.run(spec(), cwd=repo, runner=fake_runner(blocked))
    assert rep["status"] == "blocked" and rep["verified"] is False
    assert claude_task.ledger_summary(repo)["open_blockers"][0]["blockers"] == ["AGENTMAIL_API_KEY is not set locally"]

    runner = fake_runner(answer())
    again = claude_task.run(spec(), cwd=repo, runner=runner)
    assert again["status"] == "refused_retry" and "AGENTMAIL_API_KEY" in again["error"] and runner.calls == []
    assert claude_task.run(spec(key="other"), cwd=repo, runner=fake_runner(answer()))["status"] == "verified"

    retried = claude_task.run(spec(new_information="key added to .env"), cwd=repo, runner=fake_runner(answer()))
    assert retried["status"] == "verified" and claude_task.ledger_summary(repo)["open_blockers"] == []


def test_two_failures_then_the_key_is_refused(repo):
    failing = fake_runner(answer(), test_rc=1)
    assert claude_task.run(spec(), cwd=repo, runner=failing)["status"] == "failed_test"
    assert claude_task.run(spec(), cwd=repo, runner=failing)["status"] == "failed_test"
    third = claude_task.run(spec(), cwd=repo, runner=failing)
    assert third["status"] == "refused_retry" and "failed 2 times" in third["error"] and len(failing.calls) == 4


@pytest.mark.parametrize("stdout,rc,timed_out,needle", [
    ("", -1, True, "timed out"),
    (json.dumps({"result": "Reached max turns", "is_error": True, "subtype": "error_max_turns"}), 1, False, "max turns"),
    ("not json", 0, False, "exit 0"),
])
def test_failures_are_not_ok(repo, stdout, rc, timed_out, needle):
    rep = claude_task.run(spec(), cwd=repo, runner=fake_runner(stdout, rc, timed_out))
    assert rep["ok"] is False and rep["status"] == "worker_error" and needle in rep["error"].lower()


def test_missing_claude_binary(repo, monkeypatch):
    monkeypatch.setattr(base, "which", lambda b: None)
    rep = claude_task.run(spec(), cwd=repo, runner=fake_runner("{}"))
    assert rep["ok"] is False and "not installed" in rep["error"]


def test_metered_keys_never_reach_claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-would-cost-money")
    assert "ANTHROPIC_API_KEY" not in base.scrubbed_env()
    assert claude_task.base.run_cli is base.run_cli     # the handoff uses the scrubbing runner


def test_claude_gets_the_project_python_first_on_path(repo):
    env = claude_task.project_env({"BRIGHTREACH_PYTHON": "/opt/proj/bin/python", "PATH": "/hermes/venv"})
    assert env["PATH"].split(claude_task.os.pathsep)[0] == str(Path("/opt/proj/bin"))
    assert env["PATH"].endswith("/hermes/venv")
    runner = fake_runner(answer())
    assert claude_task.run(spec(), cwd=repo, runner=runner)["ok"] is True
    assert all("PATH" in c["extra_env"] for c in runner.calls)


def test_cli_needs_a_spec_and_planner_can_record_a_blocker(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit):
        claude_task.main([])
    monkeypatch.setattr(claude_task, "ROOT", tmp_path)
    assert claude_task.main(["--block", "send-domain", "DNS login needed from owner"]) == 0
    capsys.readouterr()
    assert claude_task.main(["--ledger"]) == 0
    assert json.loads(capsys.readouterr().out)["open_blockers"][0]["key"] == "send-domain"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert claude_task.main(["--spec", str(bad)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "invalid_spec"
