"""rclone copy and cryptcheck. Commands carry a remote name, never a token."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from cloudos.glasses.archive.upload import AuthExpired, StorageFull, TransportError


def copy_args(source: Path, dest: str, *, config: Path | None = None) -> list[str]:
    """``rclone copy`` of one file. Not sync."""
    args = ["rclone"]
    if config is not None:
        args.extend(["--config", str(config)])
    args.extend(["copyto", str(source), dest])
    return args


def cryptcheck_args(source: Path, dest: str, *, config: Path | None = None) -> list[str]:
    args = ["rclone"]
    if config is not None:
        args.extend(["--config", str(config)])
    args.extend(["cryptcheck", str(source), dest, "--one-way"])
    return args


def _reject_remote(remote: str) -> str:
    cleaned = remote.strip()
    if not cleaned or ":" not in cleaned:
        raise TransportError("rclone remote must look like name:path")
    lowered = cleaned.lower()
    if any(word in lowered for word in ("token", "secret", "password", "ya29.")):
        raise TransportError("refusing a remote string that contains a credential")
    return cleaned


class RcloneTransport:
    """Shell out to rclone. Used only when the owner has already authorized a remote."""

    def __init__(self, remote: str, *, config: Path | None = None, runner=None) -> None:
        self.remote = _reject_remote(remote)
        self.config = config
        self.runner = runner or _subprocess_runner

    def copy(self, local: Path, dest: str) -> None:
        full = self._dest(dest)
        result = self.runner(copy_args(local, full, config=self.config))
        _raise_for_status(result)

    def exists(self, dest: str) -> bool:
        # A missing object is a copy, not a delete. lsjson failure means absent.
        result = self.runner(self._base("lsjson", self._dest(dest)))
        return result.get("code") == 0

    def fetch(self, dest: str) -> bytes:
        target = Path(dest).name
        # Caller compares hashes. The real fetch writes to a temp file via cat.
        result = self.runner(self._base("cat", self._dest(dest)))
        if result.get("code") != 0:
            _raise_for_status(result)
        data = result.get("stdout", b"")
        if isinstance(data, str):
            data = data.encode("utf-8")
        return data

    def cryptcheck(self, source: Path, dest: str) -> None:
        result = self.runner(cryptcheck_args(source, self._dest(dest), config=self.config))
        _raise_for_status(result)

    def _dest(self, dest: str) -> str:
        if dest.startswith(self.remote):
            return dest
        return f"{self.remote.rstrip('/')}/{dest.lstrip('/')}"

    def _base(self, *parts: str) -> list[str]:
        args = ["rclone"]
        if self.config is not None:
            args.extend(["--config", str(self.config)])
        args.extend(parts)
        return args


def probe() -> dict:
    """What this process can see. The Windows host was not opened."""
    return {
        "windows_workspace_inspected": False,
        "windows_reason": "this process is not the owner's Windows machine",
        "rclone_on_path": shutil.which("rclone") is not None,
        "remote_configured": bool(os.environ.get("GLASSES_ARCHIVE_RCLONE_REMOTE", "").strip()),
        "passphrase_configured": bool(os.environ.get("GLASSES_ARCHIVE_PASSPHRASE", "").strip()),
        "authenticated_upload": False,
        "hardware_verified": False,
        "paid_inference": False,
    }


def _subprocess_runner(args: list[str]) -> dict:
    if shutil.which("rclone") is None:
        raise TransportError("rclone is not installed")
    completed = subprocess.run(args, capture_output=True, check=False)  # noqa: S603 — args are built here
    return {"code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def _raise_for_status(result: dict) -> None:
    if result.get("code") == 0:
        return
    text = result.get("stderr") or b""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    lowered = text.lower()
    if "auth" in lowered or "token" in lowered or "401" in lowered:
        raise AuthExpired("rclone remote authorization expired")
    if "quota" in lowered or "storage" in lowered or "403" in lowered:
        raise StorageFull("rclone reported no space")
    raise TransportError(text or "rclone failed")
