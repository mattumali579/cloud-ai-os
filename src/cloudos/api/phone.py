"""DB-independent task execution for the private Tailscale phone interface."""
from __future__ import annotations

from cloudos.contracts import CloudOSError, ErrorCode
from cloudos.router.providers import codex_cli
from cloudos.router.providers import ProviderError, ProviderUnavailable
from cloudos.worker.task_exec import handle_task_run


def execute_phone_task(task: str, agent: str) -> dict:
    """Execute one private task without touching the Postgres job queue.

    ``claude`` uses the existing host task executor, including its isolated
    workspace and metered-credential scrubbing. ``codex`` uses the existing
    subscription CLI provider. Auto prefers the execution-capable Claude path,
    then falls back to Codex if Claude is unavailable.
    """
    task = task.strip()
    if not task:
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "task is required")

    if agent in ("auto", "claude"):
        try:
            result = handle_task_run({"task": task})
            return {
                "agent": "claude",
                "output": str(result.get("output") or ""),
                "files_created": list(result.get("files_created") or []),
                "duration_s": result.get("duration_s"),
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
