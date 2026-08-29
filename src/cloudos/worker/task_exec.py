"""``task.run`` handler — executes a real task via the subscription-authenticated
Claude Code CLI.

Runs on the HOST worker only: the CLI and its subscription credentials live in the
user profile, not in a container image. The containerised worker never sees this
job type (see worker/loop.py handler filtering).

Billing invariant: every metered credential is scrubbed from the child environment
before exec, so this path is structurally incapable of incurring incremental API
charges. Subscription (OAuth) auth is the only thing left for the CLI to use.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cloudos.contracts import CloudOSError, ErrorCode

log = logging.getLogger("cloudos.worker.task_exec")

# Scrubbed from the child env — any of these would silently switch the CLI from
# subscription auth to metered API billing (see docs/SUBSCRIPTION_PROVIDERS.md).
METERED_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
)

ALLOWED_TOOLS = "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch,TodoWrite"

# The workspace sits inside the vault, so the vault CLAUDE.md applies and would
# otherwise force a multi-section report into a chat reply. Steer to chat shape.
CHAT_STYLE = (
    "Your reply is delivered straight into a Discord chat message. Answer in at most "
    "4 short sentences of plain English, no headings, no bullet-point templates, no "
    "status-report sections. Say what you did and what the result was. Do the work "
    "fully before replying."
)

DEFAULT_WORKSPACE_ROOT = Path.home() / "AI-Second-Brain" / "Brain_Vault" / "Task_Runs"
DEFAULT_TIMEOUT_S = 900
MAX_TIMEOUT_S = 3600
OUTPUT_CAP = 60_000  # keep a runaway transcript out of the jobs table


def _workspace_root() -> Path:
    return Path(os.environ.get("TASK_WORKSPACE_ROOT") or DEFAULT_WORKSPACE_ROOT)


def _find_cli() -> str:
    explicit = os.environ.get("CLAUDE_CLI_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("claude")
    if not found:
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "claude CLI not found on this worker",
            {"hint": "task.run must run on the host worker, not in a container"},
        )
    return found


def _child_env() -> dict:
    env = dict(os.environ)
    for key in METERED_ENV:
        env.pop(key, None)
    return env


def handle_task_run(payload: dict) -> dict:
    payload = payload or {}
    task = (payload.get("task") or "").strip()
    if not task:
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "task is required", {"field": "task"})

    try:
        timeout_s = int(payload.get("timeout_s") or DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR, "timeout_s must be an integer", {"field": "timeout_s"}
        ) from None
    timeout_s = max(30, min(timeout_s, MAX_TIMEOUT_S))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    workspace = _workspace_root() / f"{stamp}-{uuid.uuid4().hex[:6]}"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "TASK.txt").write_text(task, encoding="utf-8")

    cli = _find_cli()
    argv = [
        cli, "-p", task,
        "--output-format", "json",
        "--allowedTools", ALLOWED_TOOLS,
        "--permission-mode", "acceptEdits",
        "--append-system-prompt", CHAT_STYLE,
    ]

    log.info("task.run starting in %s (timeout %ss)", workspace, timeout_s)
    started = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(workspace),
            env=_child_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            f"task timed out after {timeout_s}s",
            {"workspace": str(workspace)},
        ) from None

    duration_s = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    output, session_id, is_error = stdout, None, False
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            output = (parsed.get("result") or "").strip() or stdout
            session_id = parsed.get("session_id")
            is_error = bool(parsed.get("is_error"))

    if proc.returncode != 0 or is_error:
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            f"task failed: {(output or stderr or 'no output')[:400]}",
            {"workspace": str(workspace), "exit_code": proc.returncode},
        )

    files = sorted(p.name for p in workspace.iterdir() if p.name != "TASK.txt")
    (workspace / "RESULT.md").write_text(
        f"# Task\n\n{task}\n\n# Result\n\n{output}\n", encoding="utf-8"
    )

    return {
        "output": output[:OUTPUT_CAP],
        "truncated": len(output) > OUTPUT_CAP,
        "workspace": str(workspace),
        "files_created": files,
        "duration_s": duration_s,
        "session_id": session_id,
    }
