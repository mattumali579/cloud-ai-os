"""Small, fixed-command Higgsfield bridge.

Only explicit, confirmed image/video requests can spend Higgsfield credits.
User text is passed as one argv value; no shell is involved.
"""
from __future__ import annotations

import shutil

from cloudos.config import get_settings
from cloudos.contracts import CloudOSError, ErrorCode
from cloudos.router.providers import base


KINDS = {
    "image": ("gpt_image_2_5", ["--aspect_ratio", "4:5", "--quality", "high"]),
    "video": ("seedance_2_5", ["--aspect_ratio", "9:16", "--duration", "5", "--resolution", "1080p", "--mode", "t2v"]),
}


def available() -> bool:
    return shutil.which(get_settings().higgsfield_cli_bin) is not None


def generate(kind: str, prompt: str, *, confirmed: bool) -> str:
    settings = get_settings()
    clean_kind = kind.strip().lower()
    if clean_kind not in KINDS:
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "Higgsfield kind must be image or video")
    if not confirmed:
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR,
            "generation needs explicit confirmation; use !higgsfield confirm <image|video> <prompt>",
        )
    if not settings.higgsfield_allow_generation:
        raise CloudOSError(
            ErrorCode.PAID_DISABLED,
            "Higgsfield generation is disabled; set HIGGSFIELD_ALLOW_GENERATION=true after reviewing credit use",
        )
    binary = shutil.which(settings.higgsfield_cli_bin)
    if not binary:
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Higgsfield CLI is not installed; install @higgsfield/cli and run higgsfield auth login",
        )
    if not prompt.strip():
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "Higgsfield prompt cannot be empty")
    model, defaults = KINDS[clean_kind]
    argv = [
        binary,
        "generate",
        "create",
        model,
        "--prompt",
        prompt.strip(),
        *defaults,
        "--wait",
        "--json",
        "--no-color",
    ]
    result = base.RUNNER(argv, timeout=settings.higgsfield_timeout_seconds)
    if result.timed_out:
        raise CloudOSError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Higgsfield generation timed out")
    if result.returncode != 0:
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Higgsfield generation failed",
            {"exit_code": result.returncode},
        )
    output = result.stdout.strip()
    if not output:
        raise CloudOSError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Higgsfield returned no output")
    return output
