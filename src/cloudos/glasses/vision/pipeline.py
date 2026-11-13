"""authorized_capture -> detector -> optional tracker -> speech/result."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from cloudos.glasses.vision.capture import CaptureAdapter, CaptureAuthorizationError
from cloudos.glasses.vision.detector import BlobClassDetector
from cloudos.glasses.vision.faces import FaceRecognitionDisabled, FaceRegistry
from cloudos.glasses.vision.safety import SafetyGate, SafetyStop
from cloudos.glasses.vision.tracker import BoundedTracker, TrackerCancelled
from cloudos.glasses.vision.types import VisionResult, VisionState

# Re-export for tests
__all__ = ["VisionPipeline", "VisionState", "VisionResult", "get_runtime_pipeline", "set_runtime_pipeline"]


@dataclass
class VisionPipeline:
    capture: CaptureAdapter
    detector: BlobClassDetector
    faces: FaceRegistry
    safety: SafetyGate
    glasses_status: dict[str, Any] | None = None
    _tracker: BoundedTracker | None = None
    _state: VisionState = VisionState.IDLE
    _last_label: str | None = None
    _visible: dict[str, Any] = field(default_factory=lambda: {
        "camera": "off",
        "tracking": False,
        "retention": "off",
        "face_recognition": "off",
        "mode": "single_shot",
    })

    def visible_state(self) -> dict[str, Any]:
        self._visible["face_recognition"] = "on" if self.faces.enabled else "off"
        self._visible["tracking"] = self._state == VisionState.TRACKING
        self._visible["camera"] = "on" if self._state in {
            VisionState.CAPTURING, VisionState.DETECTING, VisionState.TRACKING
        } else "off"
        return dict(self._visible)

    def _guard(self) -> VisionResult | None:
        try:
            self.safety.check(self.glasses_status)
        except SafetyStop as exc:
            self._state = VisionState.STOPPED_SAFETY
            self._visible["camera"] = "off"
            self._visible["tracking"] = False
            return VisionResult(state=self._state, speech=str(exc), hardware_verified=False)
        return None

    def describe(self) -> VisionResult:
        blocked = self._guard()
        if blocked:
            return blocked
        self._state = VisionState.CAPTURING
        self._visible["camera"] = "on"
        try:
            frame = self.capture.capture_photo()
        except CaptureAuthorizationError as exc:
            self._state = VisionState.BLOCKED
            self._visible["camera"] = "off"
            return VisionResult(state=self._state, speech=str(exc), hardware_verified=False)
        self._state = VisionState.DETECTING
        detections = self.detector.detect(frame)
        speech = self.detector.speak(detections)
        self._last_label = detections[0].label if detections else None
        self._state = VisionState.COMPLETE
        self._visible["camera"] = "off"
        return VisionResult(
            state=self._state,
            speech=speech,
            hardware_verified=frame.hardware_verified,
            detections=detections,
            meta={"source": frame.source},
        )

    def start_track(self, *, max_frames: int = 6) -> VisionResult:
        blocked = self._guard()
        if blocked:
            return blocked
        self._state = VisionState.TRACKING
        self._visible["camera"] = "on"
        self._visible["tracking"] = True
        self._visible["mode"] = "bounded_low_fps"
        self._tracker = BoundedTracker(self.detector, max_frames=max_frames, max_fps=2.0, queue_size=2)
        try:
            frame = self.capture.capture_photo()
        except CaptureAuthorizationError as exc:
            self._state = VisionState.BLOCKED
            self._visible["camera"] = "off"
            self._visible["tracking"] = False
            return VisionResult(state=self._state, speech=str(exc), hardware_verified=False)
        # Short session: reuse the same authorized frame as a single-shot start proof;
        # multi-frame hardware live is only used when an async Visor feed is wired.
        frames = [frame] * min(max_frames, 3)
        updates = []
        try:
            for update in self._tracker.run(frames):
                blocked = self._guard()
                if blocked:
                    self.stop_track()
                    return blocked
                updates.append(update)
        except TrackerCancelled:
            self._state = VisionState.STOPPED
            self._visible["tracking"] = False
            self._visible["camera"] = "off"
            return VisionResult(state=self._state, speech="Tracking cancelled.", hardware_verified=False)
        self._state = VisionState.COMPLETE
        self._visible["tracking"] = False
        self._visible["camera"] = "off"
        if not updates:
            return VisionResult(state=self._state, speech="Tracking session ended with no updates.", hardware_verified=frame.hardware_verified)
        last = updates[-1]
        label = last.detections[0].label if last.detections else "target"
        self._last_label = label if last.detections else self._last_label
        return VisionResult(
            state=self._state,
            speech=f"Started short track on {label}. {last.speech} Session bounded and stopped.",
            hardware_verified=frame.hardware_verified,
            detections=last.detections,
            meta={"frames": len(updates), "dropped": last.dropped_frames},
        )

    def stop_track(self) -> VisionResult:
        if self._tracker is not None:
            self._tracker.cancel()
        self._state = VisionState.STOPPED
        self._visible["tracking"] = False
        self._visible["camera"] = "off"
        self._visible["mode"] = "single_shot"
        return VisionResult(state=self._state, speech="Tracking stopped.", hardware_verified=False)

    def identify_enrolled(self) -> VisionResult:
        blocked = self._guard()
        if blocked:
            return blocked
        if not self.faces.enabled:
            return VisionResult(
                state=VisionState.BLOCKED,
                speech="Face recognition is off by default. Enable it only for consenting enrolled people.",
                hardware_verified=False,
            )
        try:
            frame = self.capture.capture_photo()
        except CaptureAuthorizationError as exc:
            return VisionResult(state=VisionState.BLOCKED, speech=str(exc), hardware_verified=False)
        try:
            match = self.faces.identify(frame)
        except FaceRecognitionDisabled as exc:
            return VisionResult(state=VisionState.BLOCKED, speech=str(exc), hardware_verified=False)
        return VisionResult(
            state=VisionState.COMPLETE,
            speech=match.speech,
            hardware_verified=frame.hardware_verified,
            meta={"face_status": match.status, "spoken_as_fact": match.spoken_as_fact},
        )

    def enroll_person(self, display_name: str, *, consent: bool) -> VisionResult:
        blocked = self._guard()
        if blocked:
            return blocked
        if not self.faces.enabled:
            return VisionResult(
                state=VisionState.BLOCKED,
                speech="Face recognition is off. Turn it on before enrolling a consenting person.",
                hardware_verified=False,
            )
        try:
            frame = self.capture.capture_photo()
            person = self.faces.enroll(frame, display_name=display_name, consent=consent)
        except (CaptureAuthorizationError, FaceRecognitionDisabled, PermissionError, ValueError) as exc:
            return VisionResult(state=VisionState.BLOCKED, speech=str(exc), hardware_verified=False)
        return VisionResult(
            state=VisionState.COMPLETE,
            speech=f"Enrolled {person.display_name} locally with consent. Data stays encrypted on this machine.",
            hardware_verified=False,
        )

    def remove_person(self, display_name: str) -> VisionResult:
        removed = self.faces.remove(display_name)
        if removed:
            return VisionResult(state=VisionState.COMPLETE, speech=f"Removed enrolled person {display_name}.")
        return VisionResult(state=VisionState.COMPLETE, speech=f"No enrolled person named {display_name} was found.")


_RUNTIME: VisionPipeline | None = None


def set_runtime_pipeline(pipeline: VisionPipeline | None) -> None:
    global _RUNTIME
    _RUNTIME = pipeline


def get_runtime_pipeline() -> VisionPipeline | None:
    return _RUNTIME


def parse_enroll_name(text: str) -> str | None:
    m = re.search(r"\benroll(?:\s+this)?(?:\s+person)?\s+as\s+([a-z0-9 .'-]+)\b", text, re.I)
    if m:
        return m.group(1).strip(" .")
    return None


def parse_remove_name(text: str) -> str | None:
    m = re.search(r"\bremove\s+enrolled\s+person\s+([a-z0-9 .'-]+)\b", text, re.I)
    if m:
        return m.group(1).strip(" .")
    return None
