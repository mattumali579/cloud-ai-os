"""Short object tracking over already-decoded frames."""

from __future__ import annotations

from dataclasses import dataclass, field

from cloudos.glasses.vision.detector import Detection
from cloudos.glasses.vision.safety import SafetyLimits, heat_or_battery


@dataclass
class TrackUpdate:
    state: str
    speech: str
    stop_reason: str | None = None
    dropped: bool = False
    label: str | None = None
    track_id: str | None = None
    accepted_frames: int = 0


@dataclass
class TrackingSession:
    limits: SafetyLimits
    state: str = "idle"
    stop_reason: str | None = None
    accepted_frames: int = 0
    misses: int = 0
    started_at: float | None = None
    last_accept_at: float | None = None
    label: str | None = None
    last_center: tuple[float, float] | None = None
    track_id: str | None = None
    events: list[str] = field(default_factory=list)

    def start(self, now: float) -> TrackUpdate:
        if self.state == "running":
            return self._update("Already tracking.", dropped=False)
        self.state = "running"
        self.stop_reason = None
        self.accepted_frames = 0
        self.misses = 0
        self.started_at = now
        self.last_accept_at = None
        self.label = None
        self.last_center = None
        self.track_id = "track-1"
        self.events.append("start")
        return self._update(
            "Tracking is on. I will stop within "
            f"{int(self.limits.max_session_s)} seconds, or sooner if you say stop "
            "or the glasses get hot.",
        )

    def cancel(self) -> TrackUpdate:
        return self._stop("user", "Stopped tracking.")

    def push(
        self,
        detections: list[Detection],
        *,
        now: float,
        image_width: int,
        image_height: int,
        thermal_c: float | None = None,
        thermal_state: str | None = None,
        battery_percent: int | None = None,
    ) -> TrackUpdate:
        if self.state != "running":
            self.start(now)

        blocked = heat_or_battery(
            self.limits,
            thermal_c=thermal_c,
            thermal_state=thermal_state,
            battery_percent=battery_percent,
        )
        if blocked == "thermal":
            return self._stop("thermal", "Stopped. The glasses are too warm.")
        if blocked == "battery":
            return self._stop("battery", "Stopped. The phone battery is low.")
        assert self.started_at is not None
        if now - self.started_at >= self.limits.max_session_s:
            return self._stop("timeout", "Stopped. That tracking window is over.")
        if self.accepted_frames >= self.limits.max_frames:
            return self._stop("max_frames", "Stopped. That was the frame limit for this session.")
        if (
            self.last_accept_at is not None
            and now - self.last_accept_at < self.limits.min_interval_s
        ):
            self.events.append("drop")
            return self._update("Skipping a frame so the glasses don't overheat.", dropped=True)

        chosen = self._choose(detections)
        self.last_accept_at = now
        self.accepted_frames += 1
        self.events.append("frame")
        if chosen is None:
            self.misses += 1
            if self.misses >= self.limits.lost_after_misses:
                return self._stop("lost", "Stopped. I lost the object.")
            return self._update("I don't see the object in this frame.")

        self.misses = 0
        if self.label is None:
            self.label = chosen.label
        center = _center(chosen)
        motion = _motion(self.last_center, center, image_width, image_height)
        self.last_center = center
        if motion == "steady":
            speech = f"The {self.label} is still there."
        else:
            speech = f"The {self.label} is still there, moving {motion}."
        return self._update(speech)

    def _choose(self, detections: list[Detection]) -> Detection | None:
        if self.label is None:
            return detections[0] if detections else None
        same = [item for item in detections if item.label == self.label]
        return same[0] if same else None

    def _stop(self, reason: str, speech: str) -> TrackUpdate:
        self.state = "stopped"
        self.stop_reason = reason
        self.events.append(f"stop:{reason}")
        return self._update(speech)

    def _update(self, speech: str, *, dropped: bool = False) -> TrackUpdate:
        return TrackUpdate(
            state=self.state,
            speech=speech,
            stop_reason=self.stop_reason,
            dropped=dropped,
            label=self.label,
            track_id=self.track_id,
            accepted_frames=self.accepted_frames,
        )


def _center(detection: Detection) -> tuple[float, float]:
    x, y, w, h = detection.bbox
    return x + w / 2, y + h / 2


def _motion(
    previous: tuple[float, float] | None,
    current: tuple[float, float],
    width: int,
    height: int,
) -> str:
    if previous is None:
        return "steady"
    dx = current[0] - previous[0]
    dy = current[1] - previous[1]
    if abs(dx) >= width * 0.08 and abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    if abs(dy) >= height * 0.08:
        return "down" if dy > 0 else "up"
    return "steady"
