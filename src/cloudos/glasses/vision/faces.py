"""Opt-in face recognition: off by default, consenting enrollments only, local encrypted store."""

from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet
from PIL import Image

from cloudos.glasses.vision.types import CaptureFrame


class FaceRecognitionDisabled(RuntimeError):
    """Face recognition is off until explicitly enabled."""


@dataclass(frozen=True)
class FaceMatch:
    status: str  # enrolled_match | unknown | disabled
    display_name: str | None
    score: float
    spoken_as_fact: bool
    speech: str


@dataclass(frozen=True)
class EnrolledPerson:
    display_name: str
    person_id: str


class FaceRegistry:
    """Local encrypted enrollment store.

    - OFF by default (enabled=False)
    - Enrollment requires consent=True
    - Unknown-by-default; never scrapes the web or cloud biometric DBs
    - Probabilistic matches are never spoken as hard identity facts
    """

    def __init__(self, root: Path, *, enabled: bool = False):
        self.root = Path(root)
        self.enabled = enabled
        self.root.mkdir(parents=True, exist_ok=True)
        self._key_path = self.root / ".face_key"
        self._index_path = self.root / "index.json"
        self._restrict_permissions()

    def _restrict_permissions(self) -> None:
        # Best-effort local restriction; Windows ACLs may still apply separately.
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def _fernet(self) -> Fernet:
        if self._key_path.exists():
            key = self._key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            self._key_path.write_bytes(key)
            try:
                os.chmod(self._key_path, 0o600)
            except OSError:
                pass
        return Fernet(key)

    def _load_index(self) -> dict:
        if not self._index_path.exists():
            return {"people": []}
        return json.loads(self._index_path.read_text(encoding="utf-8"))

    def _save_index(self, data: dict) -> None:
        self._index_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(self._index_path, 0o600)
        except OSError:
            pass

    def _embedding(self, frame: CaptureFrame) -> bytes:
        """Local coarse descriptor (histogram). Not a cloud biometric; fixture-friendly."""
        image = Image.open(io.BytesIO(frame.bytes_png)).convert("RGB").resize((32, 32))
        hist = image.histogram()  # 768 ints
        # Stable compact digest for storage comparison.
        raw = ",".join(str(v) for v in hist).encode("utf-8")
        return hashlib.sha256(raw).digest() + raw[:256]

    def _score(self, a: bytes, b: bytes) -> float:
        # Compare leading hash; full hist when both present.
        if a[:32] == b[:32]:
            return 0.92
        # Hamming-ish on truncated hist payload
        payload_a, payload_b = a[32:], b[32:]
        n = min(len(payload_a), len(payload_b))
        if n == 0:
            return 0.0
        same = sum(1 for i in range(n) if payload_a[i] == payload_b[i])
        return same / n

    def enroll(self, frame: CaptureFrame, *, display_name: str, consent: bool) -> EnrolledPerson:
        if not self.enabled:
            raise FaceRecognitionDisabled("Face recognition is disabled.")
        if not consent:
            raise PermissionError("Enrollment requires explicit consent from the person being enrolled.")
        name = display_name.strip()
        if not name:
            raise ValueError("display_name required")
        person_id = secrets.token_hex(8)
        emb = self._embedding(frame)
        token = self._fernet().encrypt(emb).decode("ascii")
        data = self._load_index()
        data["people"] = [p for p in data["people"] if p.get("display_name", "").lower() != name.lower()]
        data["people"].append(
            {
                "person_id": person_id,
                "display_name": name,
                "embedding_encrypted": token,
                # Never store embedding_plain
            }
        )
        self._save_index(data)
        return EnrolledPerson(display_name=name, person_id=person_id)

    def remove(self, display_name: str) -> bool:
        data = self._load_index()
        before = len(data["people"])
        data["people"] = [p for p in data["people"] if p.get("display_name", "").lower() != display_name.strip().lower()]
        self._save_index(data)
        return len(data["people"]) < before

    def identify(self, frame: CaptureFrame) -> FaceMatch:
        if not self.enabled:
            raise FaceRecognitionDisabled("Face recognition is disabled.")
        probe = self._embedding(frame)
        fernet = self._fernet()
        best_name = None
        best_score = 0.0
        for person in self._load_index().get("people", []):
            try:
                emb = fernet.decrypt(person["embedding_encrypted"].encode("ascii"))
            except Exception:
                continue
            score = self._score(probe, emb)
            if score > best_score:
                best_score = score
                best_name = person.get("display_name")
        if best_name and best_score >= 0.75:
            return FaceMatch(
                status="enrolled_match",
                display_name=best_name,
                score=best_score,
                spoken_as_fact=False,
                speech=(
                    f"This likely matches enrolled person {best_name} "
                    f"(about {int(best_score * 100)} percent similarity). "
                    "That is not a confirmed legal identity."
                ),
            )
        return FaceMatch(
            status="unknown",
            display_name=None,
            score=best_score,
            spoken_as_fact=False,
            speech="Unknown. I only recognize people you explicitly enrolled with consent.",
        )
