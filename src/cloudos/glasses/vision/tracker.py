"""Bounded short-session object tracking with cancel + backpressure."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from cloudos.glasses.vision.detector import BlobClassDetector
from cloudos.glasses.vision.types import CaptureFrame, Detection


class TrackerCancelled(RuntimeError):
    """Tracking stopped because cancel was requested."""


@dataclass
class TrackUpdate:
    frame_index: int
    detections: list[Detection]
    dropped_frames: int = 0
    speech: str = ""


@dataclass
class BoundedTracker:
    detector: BlobClassDetector
    max_frames: int = 8
    max_fps: float = 2.0
    queue_size: int = 2
    state: str = "idle"
    _cancel: bool = False
    _queue: deque[CaptureFrame] = field(default_factory=deque)
    _dropped: int = 0

    def cancel(self) -> None:
        self._cancel = True
        self.state = "stopped"

    def enqueue_with_backpressure(self, frames: Iterable[CaptureFrame]) -> int:
        """Push frames; drop oldest when over queue_size. Returns dropped count."""
        dropped = 0
        for frame in frames:
            if len(self._queue) >= self.queue_size:
                self._queue.popleft()
                dropped += 1
                self._dropped += 1
            self._queue.append(frame)
        return dropped

    def run(self, frames: Iterable[CaptureFrame]) -> Iterator[TrackUpdate]:
        if self._cancel:
            self.state = "stopped"
            raise TrackerCancelled("Tracking cancelled.")
        self.state = "tracking"
        min_interval = 1.0 / max(self.max_fps, 0.1)
        last_t = 0.0
        for index, frame in enumerate(frames):
            if self._cancel:
                self.state = "stopped"
                raise TrackerCancelled("Tracking cancelled.")
            if index >= self.max_frames:
                break
            now = time.monotonic()
            if last_t and now - last_t < min_interval:
                # Backpressure: skip this frame to honor low FPS / thermal budget.
                self._dropped += 1
                continue
            last_t = now
            detections = self.detector.detect(frame)
            label = detections[0].label if detections else "nothing clear"
            conf = detections[0].confidence if detections else 0.0
            speech = f"Still tracking {label} ({int(conf * 100)} percent)." if detections else "Target unclear."
            yield TrackUpdate(
                frame_index=index,
                detections=detections,
                dropped_frames=self._dropped,
                speech=speech,
            )
        self.state = "completed" if not self._cancel else "stopped"
