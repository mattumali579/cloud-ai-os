#!/usr/bin/env python
"""Hand ONE bounded coding task to Claude Code and report what really changed.

Used by the Hermes operator (docs/HERMES_REVENUE_MISSION.md):

    python scripts/claude_task.py "Fix X in src/cloudos/y.py. Run python -m pytest tests/test_y.py -q."
    python scripts/claude_task.py --task-file task.txt --max-turns 25 --timeout 1200

Runs `claude -p` on the subscription login (metered API keys are scrubbed from
the environment), in this repository, with a turn limit and a wall-clock limit.
Claude may edit files and run tests / read-only git. It may not push, send
email, or touch schedules - the operator verifies and ships.

Prints one JSON object: ok, result (Claude's words - NOT evidence),
files_changed (from git - the evidence), turns, seconds, log.
"""
from __future__ import annotations

import argparse
import json
import os
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
#: Never, even if a user-level setting would allow it. The operator ships; Claude builds.
DISALLOWED_TOOLS = [
    "Bash(git push:*)", "Bash(git commit:*)", "Bash(gh:*)", "Bash(python outreach_sender.py:*)",
    "CronCreate", "RemoteTrigger", "ScheduleWakeup",
]
RULES = (
    "You are the coding worker for the BrightReach operator. Do exactly the task below in this repository, "
    "with the smallest change that works. Do not weaken dedupe, unsubscribe, bounce suppression, send caps, "
    "QA or verification gates. Do not send email, push, commit, or create schedules. Never print secrets. "
    "Run tests only as `python -m pytest ...` (that exact form is pre-approved and uses the project's Python). "
    "Run the relevant tests and finish with: what you changed, the exact test command, and its result.\n\nTASK:\n"
)


def build_argv(task: str, max_turns: int, claude_bin: str = "claude") -> list[str]:
    return [
        claude_bin, "-p", RULES + task,
        "--max-turns", str(max_turns),
        "--permission-mode", "acceptEdits",
        "--output-format", "json",
        "--allowedTools", *ALLOWED_TOOLS,
        "--disallowedTools", *DISALLOWED_TOOLS,
    ]


def project_env(environ=None) -> dict:
    """PATH with the project's Python first, so Claude's `python -m pytest` uses the interpreter that has
    the project's packages. Inside Hermes, plain `python` is Hermes's own (no pytest)."""
    environ = os.environ if environ is None else environ
    py = environ.get("BRIGHTREACH_PYTHON") or sys.executable
    return {"PATH": str(Path(py).parent) + os.pathsep + environ.get("PATH", "")}


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


def run(task: str, *, max_turns: int = 15, timeout: int = 900, cwd: Path = ROOT, runner=None) -> dict:
    runner = runner or base.run_cli
    claude_bin = base.which("claude")
    if not claude_bin:
        return {"ok": False, "error": "claude is not installed or not on PATH", "files_changed": []}
    before = changed_files(cwd)
    started = time.time()
    res = runner(build_argv(task, max_turns, claude_bin), timeout=timeout, cwd=str(cwd), extra_env=project_env())
    seconds = round(time.time() - started, 1)
    after = changed_files(cwd)
    data = parse_result(res.stdout)

    log_dir = cwd / "logs" / "hermes"          # logs/ is gitignored - never reaches the public repo
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"claude_task_{time.strftime('%Y%m%d_%H%M%S')}.json"
    log.write_text(json.dumps({"task": task, "returncode": res.returncode, "timed_out": res.timed_out,
                               "claude": data, "stderr": res.stderr[-4000:]}, indent=2), encoding="utf-8")

    ok = res.returncode == 0 and not res.timed_out and bool(data) and not data.get("is_error")
    report = {
        "ok": ok,
        "result": str(data.get("result", ""))[:6000],
        "files_changed": sorted(p for p, s in after.items() if before.get(p) != s),
        "turns": data.get("num_turns"),
        "stop": data.get("subtype") or data.get("terminal_reason"),
        "seconds": seconds,
        "log": str(log.relative_to(cwd)),
    }
    if not ok:
        report["error"] = ("timed out" if res.timed_out else
                           (res.stderr.strip()[-600:] or str(data.get("result", ""))[:600] or f"exit {res.returncode}"))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", nargs="?", help="the task, in one quoted string")
    ap.add_argument("--task-file", help="read the task from a file instead ('-' = stdin)")
    ap.add_argument("--max-turns", type=int, default=15)
    ap.add_argument("--timeout", type=int, default=900, help="seconds")
    a = ap.parse_args(argv)
    if a.task_file:
        task = sys.stdin.read() if a.task_file == "-" else Path(a.task_file).read_text(encoding="utf-8")
    else:
        task = a.task or ""
    if not task.strip():
        ap.error("give a task or --task-file")
    report = run(task.strip(), max_turns=a.max_turns, timeout=a.timeout)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
