#!/usr/bin/env python
"""Hand ONE bounded task to Claude Code (the worker) and return evidence to the planner.

The planner is Hermes on the ChatGPT/OpenAI subscription (docs/HERMES_REVENUE_MISSION.md).
Claude Code never chooses work: it receives one task spec, executes it, and answers in a fixed form.

    python scripts/claude_task.py --spec logs/hermes/tasks/fix-x.json
    python scripts/claude_task.py --ledger                       # open blockers + recent tasks
    python scripts/claude_task.py --block KEY "exact blocker"    # planner records a blocker it found itself

A task spec is a JSON object and every field is required:

    key           short stable slug for the problem (the retry guard counts attempts per key)
    objective     the one outcome wanted
    files         the files Claude should read / may change
    constraints   what must not change or be weakened
    success_test  ONE command this script runs itself afterwards: `python -m pytest ...`,
                  `python -m ruff ...`, or a read-only status command
    evidence      what must be shown for the task to count as done
    new_information   only when retrying a key that is blocked or has failed twice: what changed

Runs `claude -p` on the subscription login (metered API keys are scrubbed from the environment),
with a turn limit and a wall-clock limit. Claude may edit files and run tests / read-only git. It
may not push, commit, send email, or touch schedules - the planner verifies and ships.

Prints one JSON object. `evidence` is measured by this script (git + the success test it ran
itself). `worker_claims` is Claude's own structured answer and is NOT evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cloudos.router.providers import base  # noqa: E402

#: What the delegated Claude run may do without asking. Everything else is denied in -p mode.
ALLOWED_TOOLS = [
    "Read", "Glob", "Grep", "Edit", "Write",
    "Bash(python -m pytest:*)", "PowerShell(python -m pytest:*)", "Bash(python -m ruff:*)",
    "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
]
#: Never, even if a user-level setting would allow it. The planner ships; Claude builds.
DISALLOWED_TOOLS = [
    "Bash(git push:*)", "Bash(git commit:*)", "Bash(gh:*)", "Bash(python outreach_sender.py:*)",
    "CronCreate", "RemoteTrigger", "ScheduleWakeup", "Agent",
]
RULES = (
    "You are the execution worker for the BrightReach planner. You do not choose work: do exactly the one "
    "task below in this repository, with the smallest change that works, and nothing else. Do not start "
    "other work and do not propose next tasks - the planner decides what happens next. Do not weaken dedupe, "
    "unsubscribe, bounce suppression, send caps, QA or verification gates. Do not send email, push, commit, "
    "or create schedules. Never print secrets. Run tests only as `python -m pytest ...` (that exact form is "
    "pre-approved and uses the project's Python). If something outside your reach stops you, stop and report "
    "it in `blockers` with the exact error text instead of working around it. Your answer is checked against "
    "git and against a fresh run of the success test, so report only what actually happened: anything you "
    "did not run or could not confirm goes in `unverified`.\n\n"
)
SPEC_LISTS = ("files", "constraints", "evidence")
SPEC_TEXT = ("key", "objective", "success_test")
#: The fixed form of Claude's answer (claude --json-schema).
RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["what_changed", "files_changed", "commands_run", "actual_results", "blockers", "unverified"],
    "properties": {
        "what_changed": {"type": "string"},
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "commands_run": {"type": "array", "items": {"type": "string"}},
        "actual_results": {"type": "string"},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "unverified": {"type": "array", "items": {"type": "string"}},
    },
}
#: Scripts the success test may call, and the only arguments allowed after them. All read-only.
READ_ONLY_SCRIPTS = {"outreach_sender.py": ["status"], "lead_engine.py": ["status"], "outreach_status.py": []}
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")              # terminal colour codes in test output
#: A blocked key needs new information to be tried again; a failing key gets this many tries first.
MAX_BLIND_ATTEMPTS = 2


def test_argv(command: str) -> list[str] | None:
    """The success test as an argv, or None if it is not an allowed, read-only form."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if len(tokens) < 2 or tokens[0] != "python":
        return None
    if tokens[1:3] in (["-m", "pytest"], ["-m", "ruff"]):
        return tokens
    return tokens if READ_ONLY_SCRIPTS.get(tokens[1]) == tokens[2:] else None


def validate_spec(spec) -> list[str]:
    """Every problem with a task spec. Empty list = bounded and explicit enough to hand over."""
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"]
    errors = [f"{f}: required, a non-empty string" for f in SPEC_TEXT
              if not (isinstance(spec.get(f), str) and spec[f].strip())]
    errors += [f"{f}: required, a non-empty list of strings" for f in SPEC_LISTS
               if not (isinstance(spec.get(f), list) and spec[f] and all(isinstance(x, str) and x.strip() for x in spec[f]))]
    if isinstance(spec.get("success_test"), str) and spec["success_test"].strip() and not test_argv(spec["success_test"]):
        errors.append("success_test: must be `python -m pytest ...`, `python -m ruff ...`, or a read-only status "
                      "command (`python outreach_sender.py status`, `python lead_engine.py status`, `python outreach_status.py`)")
    return errors


def render_task(spec: dict) -> str:
    def bullets(items):
        return "\n".join(f"- {x}" for x in items)
    return (f"OBJECTIVE:\n{spec['objective']}\n\nRELEVANT FILES:\n{bullets(spec['files'])}\n\n"
            f"CONSTRAINTS:\n{bullets(spec['constraints'])}\n\nSUCCESS TEST (must exit 0):\n{spec['success_test']}\n\n"
            f"REQUIRED EVIDENCE:\n{bullets(spec['evidence'])}\n")


def build_argv(spec: dict, max_turns: int, claude_bin: str = "claude") -> list[str]:
    # Claude's own shell rebuilds PATH, so inside Hermes its bare `python` is Hermes's (no pytest) whatever
    # PATH we pass. Give it the project's interpreter by full path and pre-approve exactly that.
    py = project_python().replace("\\", "/")
    hint = (f"If bare `python` reports `No module named pytest`, run the same command with this interpreter "
            f"instead (also pre-approved): {py} -m pytest ...\n\n")
    return [
        claude_bin, "-p", RULES + hint + render_task(spec),
        "--max-turns", str(max_turns),
        "--permission-mode", "acceptEdits",
        "--output-format", "json",
        "--json-schema", json.dumps(RESULT_SCHEMA),
        "--allowedTools", *ALLOWED_TOOLS, f"Bash({py} -m pytest:*)", f"PowerShell({py} -m pytest:*)",
        "--disallowedTools", *DISALLOWED_TOOLS,
    ]


def project_python(environ=None) -> str:
    environ = os.environ if environ is None else environ
    return environ.get("BRIGHTREACH_PYTHON") or sys.executable


def project_env(environ=None) -> dict:
    """PATH with the project's Python first, so Claude's `python -m pytest` uses the interpreter that has
    the project's packages. Inside Hermes, plain `python` is Hermes's own (no pytest)."""
    environ = os.environ if environ is None else environ
    return {"PATH": str(Path(project_python(environ)).parent) + os.pathsep + environ.get("PATH", "")}


def changed_files(cwd: Path) -> dict[str, str]:
    """path -> porcelain status + content hash, so a re-edit of an already dirty file still shows."""
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=cwd, capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stdout
    files: dict[str, str] = {}
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        p = cwd / path
        stamp = f"{p.stat().st_size}:{p.stat().st_mtime_ns}" if p.is_file() else "gone"
        files[path] = f"{line[:2]}|{stamp}"
    return files


def parse_result(stdout: str) -> dict:
    """The last JSON object claude printed (--output-format json)."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def worker_claims(data: dict) -> dict | None:
    """Claude's structured answer, or None if it did not answer in the required form."""
    claims = data.get("structured_output")
    if not isinstance(claims, dict):
        try:
            claims = json.loads(str(data.get("result", "")))
        except json.JSONDecodeError:
            return None
    if not isinstance(claims, dict) or any(k not in claims for k in RESULT_SCHEMA["required"]):
        return None
    return {k: claims[k] for k in RESULT_SCHEMA["required"]}


def ledger_path(cwd: Path) -> Path:
    return cwd / "logs" / "hermes" / "ledger.jsonl"          # logs/ is gitignored - never reaches the public repo


def read_ledger(cwd: Path) -> list[dict]:
    path = ledger_path(cwd)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def append_ledger(cwd: Path, row: dict) -> None:
    path = ledger_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **row}) + "\n")


def retry_refusal(cwd: Path, spec: dict) -> str | None:
    """Why this key may not be tried again as-is, or None if it may."""
    if str(spec.get("new_information", "")).strip():
        return None
    streak = []                                              # attempts since the key last verified
    for row in read_ledger(cwd):
        if row.get("key") == spec["key"]:
            streak = [] if row.get("status") == "verified" else streak + [row]
    if not streak:
        return None
    if streak[-1].get("status") == "blocked":
        return f"'{spec['key']}' is blocked: {'; '.join(streak[-1].get('blockers') or ['see ledger'])}"
    if len(streak) >= MAX_BLIND_ATTEMPTS:
        return f"'{spec['key']}' has failed {len(streak)} times without verifying"
    return None


def ledger_summary(cwd: Path, recent: int = 10) -> dict:
    rows = read_ledger(cwd)
    latest = {row.get("key"): row for row in rows}
    return {
        "open_blockers": [{"key": k, "blockers": r.get("blockers", []), "at": r.get("at")}
                          for k, r in latest.items() if r.get("status") == "blocked"],
        "recent": rows[-recent:],
    }


def run_success_test(spec: dict, cwd: Path, runner, timeout: int) -> dict:
    """Run the success test here, with the project's Python. This is the planner's evidence."""
    argv = [project_python()] + test_argv(spec["success_test"])[1:]
    res = runner(argv, timeout=timeout, cwd=str(cwd), extra_env=project_env())
    return {"command": spec["success_test"], "exit_code": res.returncode, "timed_out": res.timed_out,
            "passed": res.returncode == 0 and not res.timed_out,
            "output_tail": ANSI.sub("", res.stdout +("\n" + res.stderr if res.stderr.strip() else "")).strip()[-2500:]}


def in_scope(path: str, files: list[str]) -> bool:
    path = path.replace("\\", "/")
    return any(path == f or path.startswith(f.rstrip("/") + "/") for f in (x.replace("\\", "/") for x in files))


def run(spec: dict, *, max_turns: int = 15, timeout: int = 900, test_timeout: int = 600, cwd: Path = ROOT,
        runner=None) -> dict:
    errors = validate_spec(spec)
    if errors:
        return {"status": "invalid_spec", "ok": False, "verified": False, "errors": errors,
                "next": "Fix the spec. Every field is required; nothing was run."}
    refusal = retry_refusal(cwd, spec)
    if refusal:
        return {"key": spec["key"], "status": "refused_retry", "ok": False, "verified": False, "error": refusal,
                "next": "Do not retry this as-is. Pick a different independent revenue task, or resubmit with "
                        "`new_information` saying exactly what changed since the last attempt."}
    runner = runner or base.run_cli
    claude_bin = base.which("claude")
    if not claude_bin:
        return {"key": spec["key"], "status": "worker_error", "ok": False, "verified": False,
                "error": "claude is not installed or not on PATH"}

    before = changed_files(cwd)
    started = time.time()
    res = runner(build_argv(spec, max_turns, claude_bin), timeout=timeout, cwd=str(cwd), extra_env=project_env())
    seconds = round(time.time() - started, 1)
    after = changed_files(cwd)
    data = parse_result(res.stdout)
    claims = worker_claims(data)
    files_changed = sorted(p for p, s in after.items() if before.get(p) != s)
    test = run_success_test(spec, cwd, runner, test_timeout)

    log_dir = cwd / "logs" / "hermes"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"claude_task_{time.strftime('%Y%m%d_%H%M%S')}.json"
    log.write_text(json.dumps({"spec": spec, "returncode": res.returncode, "timed_out": res.timed_out,
                               "claude": data, "stderr": res.stderr[-4000:], "success_test": test}, indent=2),
                   encoding="utf-8")

    ok = res.returncode == 0 and not res.timed_out and bool(data) and not data.get("is_error")
    blockers = [str(b) for b in (claims or {}).get("blockers", []) if str(b).strip()]
    if not ok:
        status = "worker_error"
    elif claims is None:
        status = "bad_answer"
    elif blockers:
        status = "blocked"
    elif not test["passed"]:
        status = "failed_test"
    else:
        status = "verified"
    claimed = [str(f).replace("\\", "/") for f in (claims or {}).get("files_changed", [])]
    report = {
        "key": spec["key"],
        "status": status,
        "ok": ok,
        "verified": status == "verified",
        "evidence": {
            "files_changed": files_changed,
            "changed_outside_task_files": [f for f in files_changed if not in_scope(f, spec["files"])],
            "claimed_but_not_changed": sorted(set(claimed) - set(files_changed)),
            "changed_but_not_claimed": sorted(set(files_changed) - set(claimed)),
            "success_test": test,
        },
        "worker_claims": claims,
        "turns": data.get("num_turns"),
        "stop": data.get("subtype") or data.get("terminal_reason"),
        "seconds": seconds,
        "log": str(log.relative_to(cwd)),
    }
    if not ok:
        report["error"] = ("timed out" if res.timed_out else
                           (res.stderr.strip()[-600:] or str(data.get("result", ""))[:600] or f"exit {res.returncode}"))
    elif claims is None:
        report["error"] = "Claude did not answer in the required form: " + str(data.get("result", ""))[:600]
    report["next"] = {
        "verified": "Read `evidence` (not `worker_claims`), read `git diff`, then commit and push those files and choose the next task from fresh live state.",
        "failed_test": "The success test failed when re-run. Read evidence.success_test.output_tail. One sharper retry is allowed, then move on.",
        "blocked": "Blocker recorded in the ledger. Do not retry. Choose a different independent revenue task now.",
    }.get(status, "The worker did not finish. Narrow the task once, or choose a different task.")
    append_ledger(cwd, {"key": spec["key"], "objective": spec["objective"], "status": status, "blockers": blockers,
                        "files_changed": files_changed, "test_exit_code": test["exit_code"], "log": report["log"]})
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", help="path to the task spec JSON ('-' = stdin)")
    ap.add_argument("--ledger", action="store_true", help="print open blockers and recent tasks, run nothing")
    ap.add_argument("--block", nargs=2, metavar=("KEY", "BLOCKER"), help="record a blocker the planner found itself")
    ap.add_argument("--max-turns", type=int, default=15)
    ap.add_argument("--timeout", type=int, default=900, help="seconds for Claude")
    ap.add_argument("--test-timeout", type=int, default=600, help="seconds for the success test")
    a = ap.parse_args(argv)
    if a.ledger:
        print(json.dumps(ledger_summary(ROOT), indent=2))
        return 0
    if a.block:
        key, blocker = (s.strip() for s in a.block)
        if not key or not blocker:
            ap.error("--block needs a key and the exact blocker text")
        append_ledger(ROOT, {"key": key, "status": "blocked", "blockers": [blocker], "recorded_by": "planner"})
        print(json.dumps({"key": key, "status": "blocked", "recorded": True}))
        return 0
    if not a.spec:
        ap.error("give --spec, --ledger, or --block")
    raw = sys.stdin.read() if a.spec == "-" else Path(a.spec).read_text(encoding="utf-8-sig")
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        spec = None
        report = {"status": "invalid_spec", "ok": False, "verified": False, "errors": [f"spec is not valid JSON: {exc}"]}
    if spec is not None:
        report = run(spec, max_turns=a.max_turns, timeout=a.timeout, test_timeout=a.test_timeout)
    print(json.dumps(report, indent=2))
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
