"""``task.run`` handler — executes a real task via a subscription-authenticated
Claude Code or Codex CLI.

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
import re
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

CODEX_VOICE_PROMPT = """You are the host-side operator for Jarvis on Matt's Windows 11 mini PC.
Execute the user's task using the existing cloud-ai-os repository and installed tools. Reuse
working integrations and verify the outcome. Do not send messages, publish, purchase, expose
secrets, weaken security, delete user data, or alter credentials. If one of those is required,
stop and say exactly what approval or manual step is needed. Do not attempt to control an
elevated window; explain that Jarvis intentionally runs at standard-user integrity instead.
Finish with a two-sentence plain English result suitable for text-to-speech; do not use Markdown.

USER TASK:
"""

UNATTENDED_BLOCKED = re.compile(
    r"\b(?:delete|remove|erase|wipe|format|send|email|message|post|publish|"
    r"buy|purchase|pay|transfer|change\s+(?:a\s+)?password|"
    r"(?:change|modify|remove|add)\s+(?:my\s+|an?\s+|the\s+)?account|"
    r"(?:disable|weaken|bypass|change|modify)\s+(?:windows\s+)?(?:security|firewall|antivirus|uac))\b",
    re.IGNORECASE,
)

NEGATED_READ_ONLY_GUARD = re.compile(
    r"\bdo\s+not\s+(?:create\s*,?\s*)?(?:delete|remove|erase)\s*,?\s*"
    r"(?:or\s+)?modify\s+anything\b|"
    r"\bwithout\s+(?:creating\s*,?\s*)?(?:deleting|removing|erasing)\s*,?\s*"
    r"(?:or\s+)?modifying\s+anything\b",
    re.IGNORECASE,
)


def unattended_blocked(task: str) -> bool:
    """Block requested risky actions, but not an explicit read-only prohibition."""
    safety_scoped = NEGATED_READ_ONLY_GUARD.sub("", task)
    return UNATTENDED_BLOCKED.search(safety_scoped) is not None


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


def _run_codex(task: str, workspace: Path, timeout_s: int) -> dict:
    """Run Codex with subscription auth and bounded writable directories."""
    if unattended_blocked(task):
        raise CloudOSError(
            ErrorCode.PRIVACY_BLOCKED,
            "unattended Codex operator refused a high-risk action; use a purpose-built confirmed workflow",
        )

    from cloudos.contracts import ProviderState
    from cloudos.router.providers import base, codex_cli

    status = codex_cli.probe()
    if status.state is not ProviderState.AVAILABLE_SUBSCRIPTION:
        code = {
            ProviderState.AUTH_REQUIRED: ErrorCode.AUTH_REQUIRED,
            ProviderState.QUOTA_EXHAUSTED: ErrorCode.QUOTA_EXHAUSTED,
            ProviderState.BILLING_RISK: ErrorCode.BILLING_RISK,
        }.get(status.state, ErrorCode.DEPENDENCY_UNAVAILABLE)
        raise CloudOSError(code, status.detail)

    settings = __import__("cloudos.config", fromlist=["get_settings"]).get_settings()
    binary = base.which(settings.codex_cli_bin) or settings.codex_cli_bin
    result_file = workspace / "CODEX_RESULT.txt"
    # The general router may use a separate Second Brain workspace. Jarvis PC
    # operations must execute in the repository that owns this worker.
    repo = Path(os.environ.get("CLOUDOS_ROOT") or Path(__file__).resolve().parents[3]).resolve()
    argv = [
        binary,
        "exec",
        "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        "--color",
        "never",
        "-C",
        str(repo),
        "--output-last-message",
        str(result_file),
        CODEX_VOICE_PROMPT + task,
    ]
    started = datetime.now(timezone.utc)
    result = base.RUNNER(argv, timeout=timeout_s, cwd=str(repo))
    duration_s = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
    combined = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if result.returncode != 0 or result.timed_out:
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            ("Codex task timed out" if result.timed_out else f"Codex task failed with exit {result.returncode}"),
            {"detail": combined[-400:]},
        )
    output = result_file.read_text(encoding="utf-8").strip() if result_file.is_file() else (result.stdout or "").strip()
    if not output:
        raise CloudOSError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Codex returned no final result")
    return {
        "output": output[:OUTPUT_CAP],
        "truncated": len(output) > OUTPUT_CAP,
        "workspace": str(workspace),
        "files_created": sorted(p.name for p in workspace.iterdir() if p.name != "TASK.txt"),
        "duration_s": duration_s,
        "provider": "codex",
        "auth_mode": status.auth_mode,
    }


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

    provider = str(payload.get("provider") or "claude").strip().lower()
    if provider == "codex":
        return _run_codex(task, workspace, timeout_s)
    if provider != "claude":
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "provider must be 'claude' or 'codex'")

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
