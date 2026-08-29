"""Shared machinery for subscription CLI providers.

The single most important function here is :func:`scrubbed_env`: every provider
subprocess runs WITHOUT the environment variables that could route a request to
a metered API. This is not defensive fluff — verified 2026-08-28:

* Claude Code in ``-p`` (headless) mode ALWAYS uses ``ANTHROPIC_API_KEY`` when
  present (official auth precedence doc).
* Codex CLI is known to silently prefer ``OPENAI_API_KEY`` over a ChatGPT
  login, with real surprise-charge reports.
* Gemini CLI treats ``GEMINI_API_KEY`` / Vertex env vars as auth methods.

``CLAUDE_CODE_OAUTH_TOKEN`` is deliberately NOT scrubbed: it is the
subscription-backed headless credential minted by ``claude setup-token``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable, Optional

#: Env vars that can select a METERED credential path in any of the CLIs.
SCRUBBED_ENV_VARS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_PROFILE",
        "ANTHROPIC_FEDERATION_RULE_ID",
        "ANTHROPIC_ORGANIZATION_ID",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_GENAI_USE_VERTEXAI",
    }
)


def scrubbed_env(extra: Optional[dict] = None) -> dict:
    """A copy of os.environ with every metered-credential variable removed."""
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV_VARS}
    if extra:
        env.update(extra)
    return env


class CliResult:
    def __init__(self, returncode: int, stdout: str, stderr: str, timed_out: bool = False):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out


def run_cli(
    argv: list[str],
    *,
    stdin_text: Optional[str] = None,
    timeout: int = 300,
    extra_env: Optional[dict] = None,
) -> CliResult:
    """Run a CLI with the scrubbed environment. Never raises on nonzero exit;
    raises FileNotFoundError only if the binary is missing (callers pre-check
    with which())."""
    try:
        proc = subprocess.run(
            argv,
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=scrubbed_env(extra_env),
        )
        return CliResult(proc.returncode, proc.stdout or "", proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return CliResult(-1, out, err, timed_out=True)


#: Injectable runner — tests replace this to avoid real subprocesses.
RUNNER: Callable[..., CliResult] = run_cli


def set_runner(fn: Optional[Callable[..., CliResult]]) -> None:
    """Test hook: replace (or with None, restore) the subprocess runner."""
    global RUNNER
    RUNNER = fn if fn is not None else run_cli


def which(binary: str) -> Optional[str]:
    return shutil.which(binary)


def home() -> str:
    return os.path.expanduser("~")
