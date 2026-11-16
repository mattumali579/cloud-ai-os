"""Session tracking ids are not durable object identities.

A tracking id is an IoU association inside one bounded session. A detector
label is a category (a color region, or a YOLO class such as "cup"). A
durable id exists only after the user names the current track and passes
an explicit consent flag. The same category on a later track does not
inherit that name.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cloudos.glasses.vision.detector import Detection

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 .'-]{0,40}$")


class ObjectConsentRequired(Exception):
    """Naming a track was asked for without an explicit consent flag."""


class ObjectNameError(ValueError):
    pass


@dataclass
class TrackedObject:
    track_id: str
    category: str
    confidence: float
    bbox: tuple[int, int, int, int]
    model_id: str
    durable_id: str | None = None
    durable_name: str | None = None
    misses: int = 0


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix0, iy0 = max(ax, bx), max(ay, by)
    ix1, iy1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


class ObjectNameLedger:
    """Consent records of names the user assigned. Not an automatic re-identifier."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def add(
        self,
        *,
        durable_id: str,
        name: str,
        category: str,
        track_id: str,
    ) -> None:
        data = self._load()
        data["names"].append(
            {
                "durable_id": durable_id,
                "name": name,
                "category": category,
                "track_id_at_naming": track_id,
                "named_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def names(self) -> list[dict]:
        return list(self._load()["names"])

    def _load(self) -> dict:
        if not self.path.is_file():
            return {"names": [], "raw_images": [], "auto_match": False}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data.setdefault("names", [])
        data["raw_images"] = []
        data["auto_match"] = False
        return data


class ObjectTrackBook:
    """Greedy IoU tracker. Ids start at ``trk-0001`` and die with the session."""

    def __init__(
        self,
        *,
        iou_threshold: float = 0.3,
        drop_after: int = 5,
        ledger: ObjectNameLedger | None = None,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.drop_after = drop_after
        self.ledger = ledger
        self._next = 1
        self.tracks: dict[str, TrackedObject] = {}

    def update(self, detections: list[Detection]) -> list[TrackedObject]:
        """Match detections to existing tracks of the same category.

        This method never reads the name ledger. A saved name cannot attach
        itself to a new track just because the category matches.
        """
        unused = set(range(len(detections)))
        for track in self.tracks.values():
            best_index = None
            best_score = self.iou_threshold
            for index in unused:
                detection = detections[index]
                if detection.label != track.category:
                    continue
                score = iou(track.bbox, detection.bbox)
                if score >= best_score:
                    best_score = score
                    best_index = index
            if best_index is None:
                track.misses += 1
                continue
            unused.remove(best_index)
            detection = detections[best_index]
            track.bbox = detection.bbox
            track.confidence = detection.confidence
            track.model_id = detection.model_id
            track.misses = 0
        for track_id, track in list(self.tracks.items()):
            if track.misses >= self.drop_after:
                del self.tracks[track_id]
        for index in sorted(unused):
            detection = detections[index]
            track_id = f"trk-{self._next:04d}"
            self._next += 1
            self.tracks[track_id] = TrackedObject(
                track_id=track_id,
                category=detection.label,
                confidence=detection.confidence,
                bbox=detection.bbox,
                model_id=detection.model_id,
            )
        return [track for track in self.tracks.values() if track.misses == 0]

    def remember(self, track_id: str, name: str, *, consent: bool) -> TrackedObject:
        if not consent:
            raise ObjectConsentRequired("naming a tracked object needs an explicit consent flag")
        cleaned = " ".join(name.split())
        if not _NAME.match(cleaned):
            raise ObjectNameError("object names use letters, numbers, spaces, and '.-")
        track = self.tracks.get(track_id)
        if track is None:
            raise ObjectNameError("that tracking id is not in this session")
        slug = re.sub(r"[^a-z0-9]+", "-", cleaned.lower()).strip("-")
        track.durable_id = f"named-{slug}"
        track.durable_name = cleaned
        if self.ledger is not None:
            self.ledger.add(
                durable_id=track.durable_id,
                name=cleaned,
                category=track.category,
                track_id=track.track_id,
            )
        return track
