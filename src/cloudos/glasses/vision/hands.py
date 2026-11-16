"""21-point hand landmarks and pinch / open-palm / fist classification.

The gesture rules are geometry on the 21 MediaPipe hand indices. They are not
the MediaPipe gesture recognizer, and they do not download a model.

MediaPipe and the official ``hand_landmarker.task`` file are Apache-2.0.
This module imports MediaPipe only when a caller constructs the optional
adapter and both the package and a local task file are already present.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from cloudos.glasses.vision.images import RgbImage

LANDMARK_COUNT = 21
WRIST = 0
THUMB_TIP = 4
INDEX_TIP = 8
MIDDLE_MCP = 9
# (fingertip, mcp) for index, middle, ring, pinky. The thumb is not in this set.
_FINGERS = ((8, 5), (12, 9), (16, 13), (20, 17))

# Sample poses are fixtures for offline tests. They are not detections from a photo.
_OPEN_PALM = (
    (0.50, 0.85),
    (0.36, 0.74),
    (0.30, 0.62),
    (0.24, 0.52),
    (0.18, 0.42),
    (0.40, 0.55),
    (0.38, 0.40),
    (0.37, 0.28),
    (0.36, 0.16),
    (0.50, 0.52),
    (0.50, 0.36),
    (0.50, 0.22),
    (0.50, 0.10),
    (0.60, 0.55),
    (0.62, 0.40),
    (0.63, 0.28),
    (0.64, 0.16),
    (0.70, 0.60),
    (0.74, 0.48),
    (0.76, 0.38),
    (0.78, 0.28),
)
_FIST = (
    (0.50, 0.78),
    (0.40, 0.70),
    (0.36, 0.62),
    (0.42, 0.66),
    (0.48, 0.70),
    (0.40, 0.52),
    (0.44, 0.60),
    (0.47, 0.66),
    (0.49, 0.72),
    (0.50, 0.50),
    (0.50, 0.60),
    (0.50, 0.68),
    (0.50, 0.74),
    (0.60, 0.52),
    (0.56, 0.60),
    (0.53, 0.66),
    (0.51, 0.72),
    (0.68, 0.58),
    (0.62, 0.64),
    (0.56, 0.70),
    (0.52, 0.74),
)


class HandLandmarkerUnavailable(RuntimeError):
    """MediaPipe or the local task file is missing. No landmarks were invented."""


@dataclass(frozen=True)
class Landmark:
    x: float
    y: float
    z: float = 0.0


@dataclass(frozen=True)
class HandPose:
    landmarks: tuple[Landmark, ...]
    handedness: str = "unknown"
    score: float = 1.0
    source: str = "sample"

    def __post_init__(self) -> None:
        if len(self.landmarks) != LANDMARK_COUNT:
            raise ValueError(f"a hand needs {LANDMARK_COUNT} landmarks")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("hand score must be between 0 and 1")


def _dist(a: Landmark, b: Landmark) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _pose_from_points(points: tuple[tuple[float, float], ...], source: str) -> HandPose:
    if len(points) != LANDMARK_COUNT:
        raise ValueError(f"a hand needs {LANDMARK_COUNT} landmarks")
    return HandPose(tuple(Landmark(x, y) for x, y in points), source=source)


def sample_open_palm() -> HandPose:
    return _pose_from_points(_OPEN_PALM, "sample")


def sample_fist() -> HandPose:
    return _pose_from_points(_FIST, "sample")


def sample_pinch(at: tuple[float, float]) -> HandPose:
    """Open palm with the thumb tip and index tip on the same normalized point."""
    x, y = at
    landmarks = []
    for index, point in enumerate(_OPEN_PALM):
        if index in (THUMB_TIP, INDEX_TIP):
            landmarks.append(Landmark(x, y))
        else:
            landmarks.append(Landmark(*point))
    return HandPose(tuple(landmarks), source="sample")


def classify_gesture(pose: HandPose) -> str:
    """Return ``pinch``, ``open_palm``, ``fist``, or ``unknown``.

    A pinch is the thumb tip meeting the index tip while the other fingers
    are not curled into a fist. A fist curls the four fingers. An open palm
    extends them. Anything else stays unknown instead of being guessed.
    """
    landmarks = pose.landmarks
    wrist = landmarks[WRIST]
    palm = _dist(wrist, landmarks[MIDDLE_MCP])
    if palm < 1e-6:
        return "unknown"
    pinch_ratio = _dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) / palm
    curled = 0
    extended = 0
    for tip_index, mcp_index in _FINGERS:
        tip_reach = _dist(landmarks[tip_index], wrist)
        mcp_reach = _dist(landmarks[mcp_index], wrist)
        if tip_reach < mcp_reach * 1.05:
            curled += 1
        if tip_reach > mcp_reach * 1.15:
            extended += 1
    if pinch_ratio < 0.45 and curled <= 2:
        return "pinch"
    if curled >= 3:
        return "fist"
    if extended >= 3 and pinch_ratio >= 0.45:
        return "open_palm"
    return "unknown"


def pinch_point(pose: HandPose) -> tuple[float, float]:
    """Normalized midpoint of the thumb tip and the index tip."""
    thumb = pose.landmarks[THUMB_TIP]
    index = pose.landmarks[INDEX_TIP]
    return (thumb.x + index.x) / 2.0, (thumb.y + index.y) / 2.0


def hand_model_status(model_path: Path | None = None) -> dict:
    """Whether the optional MediaPipe file is present. This never downloads it."""
    path = Path(model_path) if model_path else None
    return {
        "pipeline": "geometric-gestures-v1",
        "landmark_count": LANDMARK_COUNT,
        "mediapipe_license": "Apache-2.0",
        "task_file_license": "Apache-2.0",
        "mediapipe_importable": _module_importable("mediapipe"),
        "model_file_present": bool(path and path.is_file()),
        "auto_download": False,
        "classifier": "geometry on 21 landmarks, not the MediaPipe gesture recognizer",
        "hardware_verified": False,
    }


class UnavailableHandLandmarker:
    """Used when MediaPipe is not configured. ``detect`` returns no hands."""

    available = False
    kind = "unavailable"
    simulated = False

    def detect(self, image: RgbImage) -> list[HandPose]:
        del image
        return []


class ScriptedHandLandmarker:
    """Replay poses. Tests use this. It is not a glasses camera."""

    available = True
    kind = "scripted"
    simulated = True

    def __init__(self, poses: list[HandPose | None]) -> None:
        self._poses = list(poses)
        self.calls = 0

    def detect(self, image: RgbImage) -> list[HandPose]:
        del image
        if not self._poses:
            self.calls += 1
            return []
        pose = self._poses[min(self.calls, len(self._poses) - 1)]
        self.calls += 1
        if pose is None:
            return []
        return [pose]


class MediaPipeHandLandmarker:
    """Optional CPU landmark source. Requires a local ``hand_landmarker.task``.

    The task file is the official MediaPipe hand landmarker (Apache-2.0).
    Pass a path the owner already downloaded. This class does not fetch it.
    """

    kind = "mediapipe"
    simulated = False

    def __init__(self, model_path: Path) -> None:
        self.model_path = Path(model_path)
        self._landmarker = None

    @property
    def available(self) -> bool:
        return self.model_path.is_file() and _module_importable("mediapipe")

    def detect(self, image: RgbImage) -> list[HandPose]:
        if not self.available:
            raise HandLandmarkerUnavailable(
                "MediaPipe hand landmarks need the mediapipe package and a local "
                "hand_landmarker.task file. This pipeline does not download either."
            )
        import mediapipe as mp
        import numpy as np

        array = np.frombuffer(image.rgb, dtype=np.uint8).reshape(image.height, image.width, 3)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(array))
        result = self._load().detect(mp_image)
        poses: list[HandPose] = []
        handed = list(result.handedness or [])
        for index, hand in enumerate(result.hand_landmarks or []):
            points = tuple(
                Landmark(float(point.x), float(point.y), float(getattr(point, "z", 0.0)))
                for point in hand
            )
            if len(points) != LANDMARK_COUNT:
                continue
            label = "unknown"
            score = 0.0
            if index < len(handed) and handed[index]:
                category = handed[index][0]
                label = str(
                    getattr(category, "category_name", None)
                    or getattr(category, "display_name", "unknown")
                )
                score = float(getattr(category, "score", 0.0) or 0.0)
            poses.append(HandPose(points, handedness=label, score=min(1.0, max(0.0, score)), source="mediapipe"))
        return poses

    def _load(self):
        if self._landmarker is not None:
            return self._landmarker
        import mediapipe as mp

        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(self.model_path)),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_hands=1,
        )
        self._landmarker = mp.tasks.vision.HandLandmarker.create_from_options(options)
        return self._landmarker


def _module_importable(name: str) -> bool:
    try:
        __import__(name)
    except ImportError:
        return False
    return True
