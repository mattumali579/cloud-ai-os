"""Copy closed session ciphertext. Never sync, and never delete the only copy on an error."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from cloudos.glasses.archive.index import Index
from cloudos.glasses.archive.session import DEFAULT_SPOOL_BYTES

QUEUE_ALERT_FRACTION = 0.5


class TransportError(RuntimeError):
    code = "transport"


class AuthExpired(TransportError):
    code = "auth_expired"


class StorageFull(TransportError):
    code = "storage_full"


class ChecksumMismatch(TransportError):
    code = "checksum_mismatch"


class RemoteDeleteDisabled(RuntimeError):
    """Remote objects are not deleted by this uploader."""


def upload_session(
    session_dir: Path,
    transport,
    *,
    attempts: int = 4,
    sleeper=None,
) -> dict:
    """Upload one ready directory. Local files stay until every hash matches."""
    sleeper = sleeper or (lambda _seconds: None)
    state_path = Path(session_dir) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("status") == "committed":
        return {"status": "committed", "session_id": state["session_id"], "uploaded": 0, "deleted_local": False}
    prefix = state["remote_prefix"]
    copied = 0
    for item in state["files"]:
        local = Path(session_dir) / item["name"]
        dest = f"{prefix}/{item['name']}"
        digest = item["sha256"]
        if _remote_matches(transport, dest, digest):
            continue
        _copy_with_retry(transport, local, dest, attempts=attempts, sleeper=sleeper)
        remote = transport.fetch(dest)
        if hashlib.sha256(remote).hexdigest() != digest:
            raise ChecksumMismatch(dest)
        copied += 1
    state["status"] = "committed"
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return {
        "status": "committed",
        "session_id": state["session_id"],
        "uploaded": copied,
        "remote_prefix": prefix,
        "deleted_local": False,
    }


def drain(spool: Path, transport, *, alert_bytes: int | None = None, attempts: int = 4, sleeper=None) -> dict:
    """Upload every ready session. Remove a session directory only after commit."""
    root = Path(spool)
    ready = root / "ready"
    index = Index(root / "index.jsonl")
    alert_at = DEFAULT_SPOOL_BYTES // 2 if alert_bytes is None else alert_bytes
    queued = _bytes(ready)
    alerts = []
    if queued > alert_at:
        alerts.append("queue_over_threshold")
    committed = []
    errors = []
    if not ready.exists():
        return _report(committed, errors, alerts, queued)
    for state_path in sorted(ready.rglob("state.json")):
        session_dir = state_path.parent
        try:
            result = upload_session(session_dir, transport, attempts=attempts, sleeper=sleeper)
        except AuthExpired as exc:
            alerts.append("auth_expired")
            errors.append({"session": session_dir.name, "code": exc.code, "kept": True})
            break
        except StorageFull as exc:
            alerts.append("storage_full")
            errors.append({"session": session_dir.name, "code": exc.code, "kept": True})
            break
        except (TransportError, OSError) as exc:
            errors.append({"session": session_dir.name, "code": getattr(exc, "code", "transport"), "kept": True})
            continue
        if result["status"] != "committed":
            continue
        state = json.loads(state_path.read_text(encoding="utf-8"))
        index.add(state)
        shutil.rmtree(session_dir)
        result["deleted_local"] = True
        committed.append(result["session_id"])
    return _report(committed, errors, alerts, _bytes(ready))


def drain_configured(payload: dict | None = None) -> dict:
    """Host-worker entry. Missing setup is a result, not a paid call and not a retry storm."""
    remote = os.environ.get("GLASSES_ARCHIVE_RCLONE_REMOTE", "").strip()
    passphrase = os.environ.get("GLASSES_ARCHIVE_PASSPHRASE", "").strip()
    spool = Path(os.environ.get("GLASSES_ARCHIVE_SPOOL", "") or _default_spool())
    if not remote or not passphrase:
        return {
            "status": "needs_setup",
            "uploaded": 0,
            "paid_inference": False,
            "authenticated_upload": False,
            "hardware_verified": False,
        }
    from cloudos.glasses.archive.rclone import RcloneTransport

    transport = RcloneTransport(remote)
    result = drain(spool, transport)
    result["paid_inference"] = False
    result["authenticated_upload"] = True
    result["hardware_verified"] = False
    result["payload_keys"] = sorted((payload or {}).keys())
    return result


def refuse_remote_delete() -> None:
    raise RemoteDeleteDisabled("this uploader does not delete remote objects")


def _copy_with_retry(transport, local: Path, dest: str, *, attempts: int, sleeper) -> None:
    delay = 1.0
    last: Exception | None = None
    for _ in range(attempts):
        try:
            transport.copy(local, dest)
            return
        except (AuthExpired, StorageFull):
            raise
        except TransportError as exc:
            last = exc
            sleeper(delay)
            delay *= 2
    raise TransportError(str(last) if last else "copy failed")


def _remote_matches(transport, dest: str, digest: str) -> bool:
    if not transport.exists(dest):
        return False
    return hashlib.sha256(transport.fetch(dest)).hexdigest() == digest


def _bytes(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _report(committed: list[str], errors: list[dict], alerts: list[str], queued: int) -> dict:
    return {
        "status": "error" if errors else "ok",
        "committed": committed,
        "errors": errors,
        "alerts": alerts,
        "queued_bytes": queued,
        "remote_delete": False,
        "paid_inference": False,
        "hardware_verified": False,
    }


def _default_spool() -> Path:
    return Path.home() / ".local" / "share" / "cloud-ai-os" / "glasses-archive"


class ScriptedTransport:
    """In-memory stand-in. Tests use it. It is not Google Drive."""

    def __init__(
        self,
        *,
        fail_times: int = 0,
        corrupt: bool = False,
        auth: bool = False,
        full: bool = False,
        interrupt_after: int | None = None,
    ) -> None:
        self.files: dict[str, bytes] = {}
        self.copies: list[str] = []
        self.fail_times = fail_times
        self.corrupt = corrupt
        self.auth = auth
        self.full = full
        self.interrupt_after = interrupt_after
        self.calls = 0

    def exists(self, dest: str) -> bool:
        return dest in self.files

    def fetch(self, dest: str) -> bytes:
        if dest not in self.files:
            raise TransportError(f"missing {dest}")
        return self.files[dest]

    def copy(self, local: Path, dest: str) -> None:
        self.calls += 1
        if self.auth:
            raise AuthExpired("remote auth expired")
        if self.full:
            raise StorageFull("remote storage is full")
        if self.interrupt_after is not None and self.calls > self.interrupt_after:
            raise TransportError("stream interrupted")
        if self.fail_times > 0:
            self.fail_times -= 1
            raise TransportError("temporary outage")
        data = Path(local).read_bytes()
        if self.corrupt:
            data += b"x"
        self.files[dest] = data
        self.copies.append(dest)
