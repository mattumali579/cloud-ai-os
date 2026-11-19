"""Build one immutable session directory. Media stays off unless opted in."""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cloudos.glasses.archive.crypto import encrypt_bytes
from cloudos.glasses.archive.schema import SCHEMA_MANIFEST, SCHEMA_SUMMARY, SchemaError

DEFAULT_SPOOL_BYTES = 64 * 1024 * 1024
DEFAULT_CHUNK_BYTES = 256 * 1024
DEFAULT_MEDIA_BYTES = 2 * 1024 * 1024
_FACE_MARKERS = ("face", "embedding", "template", "biometric")


class SpoolFull(RuntimeError):
    pass


class MediaDisabled(RuntimeError):
    pass


class SessionBundle:
    """Events are encrypted into the ready queue. The phone is not a store."""

    def __init__(
        self,
        spool: Path,
        *,
        passphrase: str,
        session_id: str | None = None,
        now: datetime | None = None,
        allow_media: bool = False,
        max_spool_bytes: int = DEFAULT_SPOOL_BYTES,
        max_media_bytes: int = DEFAULT_MEDIA_BYTES,
        chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    ) -> None:
        self.spool = Path(spool)
        self.passphrase = passphrase
        self.session_id = session_id or str(uuid.uuid4())
        self.now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.allow_media = allow_media
        self.max_spool_bytes = max_spool_bytes
        self.max_media_bytes = max_media_bytes
        self.chunk_bytes = chunk_bytes
        self.events: list[dict] = []
        self.media: list[tuple[str, bytes]] = []
        self.labels: set[str] = set()
        self.track_ids: set[str] = set()
        self._buffer = bytearray()
        self._chunks: list[tuple[str, bytes]] = []
        self._chunk_index = 0
        day = self.now.strftime("%Y/%m/%d")
        self.relative = Path("sessions") / day / self.session_id
        self.ready_dir = self.spool / "ready" / day / self.session_id

    def add_event(self, event: dict) -> None:
        if event.get("schema") != "glasses.archive.event.v1":
            raise SchemaError("events must use glasses.archive.event.v1")
        if event.get("session_id") != self.session_id:
            raise SchemaError("event session_id does not match this bundle")
        self._guard_capacity(len(json.dumps(event)))
        line = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
        self._buffer.extend(line)
        self.events.append(event)
        for item in event.get("detections") or []:
            if item.get("class"):
                self.labels.add(item["class"])
        if event.get("track_id"):
            self.track_ids.add(event["track_id"])
        if len(self._buffer) >= self.chunk_bytes:
            self._rotate()

    def add_media(self, name: str, data: bytes) -> str:
        if not self.allow_media:
            raise MediaDisabled("raw photos and clips are off unless media opt-in is set")
        lowered = name.lower()
        if any(marker in lowered for marker in _FACE_MARKERS):
            raise MediaDisabled("face biometric files are not archived")
        if len(data) > self.max_media_bytes:
            raise MediaDisabled("that clip is over the media byte cap")
        self._guard_capacity(len(data))
        digest = hashlib.sha256(data).hexdigest()
        safe = Path(name).name
        self.media.append((safe, data))
        return digest

    def close(self) -> Path:
        """Encrypt closed chunks and drop the plaintext. The directory stays queued."""
        if self.ready_dir.exists():
            raise SchemaError("that session directory already exists")
        self._rotate()
        summary = {
            "schema": SCHEMA_SUMMARY,
            "session_id": self.session_id,
            "created_at": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event_count": len(self.events),
            "labels": sorted(self.labels),
            "track_ids": sorted(self.track_ids),
            "media_opt_in": self.allow_media,
            "media_count": len(self.media),
            "face_biometrics": False,
            "hardware_verified": False,
            "persistent_phone_store": False,
        }
        files: list[tuple[str, bytes]] = list(self._chunks)
        files.append(("summary.json", json.dumps(summary, indent=2).encode("utf-8") + b"\n"))
        for name, data in self.media:
            files.append((f"media/{name}", data))
        listed = [
            {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in files
        ]
        manifest = {
            "schema": SCHEMA_MANIFEST,
            "session_id": self.session_id,
            "created_at": summary["created_at"],
            "files": listed,
            "media_opt_in": self.allow_media,
            "face_biometrics": False,
        }
        files.append(("manifest.json", json.dumps(manifest, indent=2).encode("utf-8") + b"\n"))
        self.ready_dir.mkdir(parents=True, exist_ok=False)
        uploaded = []
        for name, data in files:
            blob = encrypt_bytes(data, self.passphrase)
            target = self.ready_dir / f"{name}.enc"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            uploaded.append(
                {
                    "name": f"{name}.enc",
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "plaintext_sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(blob),
                }
            )
        state = {
            "status": "ready",
            "session_id": self.session_id,
            "remote_prefix": self.relative.as_posix(),
            "files": uploaded,
            "labels": sorted(self.labels),
            "track_ids": sorted(self.track_ids),
            "created_at": summary["created_at"],
            "media_opt_in": self.allow_media,
        }
        (self.ready_dir / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        self._chunks.clear()
        self._buffer.clear()
        self.media.clear()
        return self.ready_dir

    def _rotate(self) -> None:
        if not self._buffer:
            return
        self._chunk_index += 1
        name = f"events-{self._chunk_index:04d}.jsonl.gz"
        self._chunks.append((name, gzip.compress(bytes(self._buffer))))
        self._buffer.clear()

    def _guard_capacity(self, extra: int) -> None:
        if _tree_bytes(self.spool) + extra > self.max_spool_bytes:
            raise SpoolFull("the local upload spool is at its byte cap; nothing was dropped")


def _tree_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def new_session_id() -> str:
    return str(uuid.uuid4())
