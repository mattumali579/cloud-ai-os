"""Opt-in face templates. Local file only. Never treated as a certain identity."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cloudos.glasses.vision.images import RgbImage

_NAME = re.compile(r"^[A-Za-z][A-Za-z .'-]{0,39}$")
MATCH_THRESHOLD = 0.92
MAX_PEOPLE = 20


class ConsentRequired(Exception):
    """Enrollment was asked for without an explicit consent flag."""


class GalleryError(ValueError):
    pass


@dataclass(frozen=True)
class FaceBox:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class Identity:
    status: str
    speech: str
    name: str | None = None
    score: float | None = None
    certain: bool = False

    def __post_init__(self) -> None:
        if self.certain:
            raise GalleryError("identity is never reported as certain")


class UnavailableFaceFinder:
    """No face detector is installed. Returning no boxes is not a guess."""

    backend = "unavailable"

    def find(self, image: RgbImage) -> list[FaceBox]:
        del image
        return []


def embed(image: RgbImage) -> list[int]:
    """8x8 RGB downsample. This is a local template, not a biometric model."""
    grid = 8
    out: list[int] = []
    for gy in range(grid):
        for gx in range(grid):
            x0 = gx * image.width // grid
            x1 = max(x0 + 1, (gx + 1) * image.width // grid)
            y0 = gy * image.height // grid
            y1 = max(y0 + 1, (gy + 1) * image.height // grid)
            total = [0, 0, 0]
            count = 0
            for y in range(y0, y1):
                for x in range(x0, x1):
                    pixel = image.pixel(x, y)
                    total[0] += pixel[0]
                    total[1] += pixel[1]
                    total[2] += pixel[2]
                    count += 1
            out.extend(channel // count for channel in total)
    return out


def _cosine(left: list[int], right: list[int]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


class FaceGallery:
    """Consenting templates in one JSON file. Raw photos are not stored."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._path = self.root / "enrollments.json"

    def enroll(self, name: str, image: RgbImage, *, consent: bool) -> Identity:
        if not consent:
            raise ConsentRequired("enrollment requires explicit consent")
        display = _clean_name(name)
        people = self._load()
        key = display.casefold()
        if key not in people and len(people) >= MAX_PEOPLE:
            raise GalleryError("the local enrollment list is full")
        people[key] = {
            "name": display,
            "embedding": embed(image),
            "consented_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save(people)
        return Identity(
            status="enrolled",
            name=display,
            certain=False,
            speech=(
                f"I saved a local template for {display}. "
                "A later match will be described as a resemblance, not a fact."
            ),
        )

    def identify(self, image: RgbImage) -> Identity:
        people = self._load()
        if not people:
            return Identity(
                status="unknown",
                speech="Unknown. Nobody is enrolled on this device.",
            )
        probe = embed(image)
        ranked = sorted(
            (
                (record["name"], _cosine(probe, record["embedding"]))
                for record in people.values()
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        best_name, best_score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        if best_score < MATCH_THRESHOLD:
            return Identity(
                status="unknown",
                score=round(best_score, 4),
                speech="Unknown. This does not match anyone who was enrolled.",
            )
        if second >= MATCH_THRESHOLD and best_score - second < 0.03:
            return Identity(
                status="ambiguous",
                score=round(best_score, 4),
                speech="I can't tell which enrolled person this resembles.",
            )
        return Identity(
            status="resembles",
            name=best_name,
            score=round(best_score, 4),
            speech=(
                f"This resembles the enrolled template for {best_name}. "
                "I am not certain."
            ),
        )

    def delete(self, name: str) -> bool:
        display = _clean_name(name)
        people = self._load()
        if display.casefold() not in people:
            return False
        del people[display.casefold()]
        self._save(people)
        return True

    def names(self) -> list[str]:
        return [record["name"] for record in self._load().values()]

    def privacy_audit(self) -> dict:
        raw = [
            path.name
            for path in self.root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".ppm", ".jpg", ".jpeg", ".png", ".heic", ".bin"}
        ]
        return {
            "people": len(self.names()),
            "raw_images": raw,
            "uploads": False,
            "store": str(self.root),
        }

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        people = payload.get("people") or {}
        if not isinstance(people, dict):
            raise GalleryError("enrollment file is unreadable")
        return people

    def _save(self, people: dict) -> None:
        temporary = self._path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"version": 1, "people": people}, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self._path)


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.strip().split())
    if not _NAME.fullmatch(cleaned):
        raise GalleryError("use a short name made of letters")
    return cleaned
