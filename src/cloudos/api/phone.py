"""DB-independent task execution for the private Tailscale phone interface."""
from __future__ import annotations

from pathlib import Path

from cloudos.contracts import CloudOSError, ErrorCode
from cloudos.router.providers import claude_cli, codex_cli
from cloudos.router.providers import ProviderError, ProviderUnavailable

PHONE_CLAUDE_TOOLS = "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch"


def execute_phone_task(task: str, agent: str) -> dict:
    """Execute one private task without touching the Postgres job queue.

    Both paths use the subscription CLI providers, whose subprocess runner
    captures output and hides Windows console windows. Auto prefers Claude,
    then falls back to Codex if Claude is unavailable.
    """
    task = task.strip()
    if not task:
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "task is required")

    if agent in ("auto", "claude"):
        try:
            result = claude_cli.generate(
                task,
                max_tokens=4096,
                timeout=900,
                allowed_tools=PHONE_CLAUDE_TOOLS,
                permission_mode="acceptEdits",
                cwd=str(Path.cwd()),
            )
            return {
                "agent": "claude",
                "output": result.text,
                "files_created": [],
                "duration_s": None,
            }
        except CloudOSError:
            if agent == "claude":
                raise

    if agent in ("auto", "codex"):
        try:
            result = codex_cli.generate(task, max_tokens=4096, timeout=300)
        except (ProviderError, ProviderUnavailable) as exc:
            raise CloudOSError(
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                f"codex task unavailable: {exc}",
            ) from exc
        return {
            "agent": "codex",
            "output": result.text,
            "files_created": [],
            "duration_s": None,
        }

    raise CloudOSError(ErrorCode.VALIDATION_ERROR, "invalid agent", {"agent": agent})
