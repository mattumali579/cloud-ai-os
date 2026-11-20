"""Shared vision types for issue #25."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class VisionState(str, Enum):
    IDLE = "idle"
    CAPTURING = "capturing"
    DETECTING = "detecting"
    TRACKING = "tracking"
    COMPLETE = "complete"
    STOPPED = "stopped"
    STOPPED_SAFETY = "stopped_safety"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass(frozen=True)
class CaptureFrame:
    bytes_png: bytes
    source: str
    hardware_verified: bool
    width: int
    height: int
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    bbox: tuple[int, int, int, int] | None = None
    identity_kind: str = "class_label"  # never "exact_product" unless proven
    product_identity: str | None = None


@dataclass
class VisionResult:
    state: VisionState
    speech: str
    hardware_verified: bool = False
    detections: list[Detection] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
