"""Frame, 21 landmarks, gesture, selected category, event, mocked voice reply.

The voice payload is the Visor ``voice.reply`` parameter object. This process
does not open the phone socket, does not call a TTS service, and does not
hand the phrase to the owner's assistant.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from cloudos.glasses.vision.hands import (
    LANDMARK_COUNT,
    UnavailableHandLandmarker,
    classify_gesture,
    pinch_point,
)
from cloudos.glasses.vision.images import RgbImage
from cloudos.glasses.vision.objects import (
    ObjectConsentRequired,
    ObjectNameError,
    ObjectNameLedger,
    ObjectTrackBook,
    TrackedObject,
)
from cloudos.glasses.vision.safety import SafetyLimits, heat_or_battery

_UNAVAILABLE = (
    "Hand landmarks need the optional MediaPipe landmarker and a local "
    "hand_landmarker.task file. Neither ran, so I did not invent a gesture."
)


class GestureObjectPipeline:
    """One bounded session: landmarks select a tracked category, then a voice line."""

    def __init__(
        self,
        store: Path,
        *,
        landmarker=None,
        detector,
        limits: SafetyLimits | None = None,
        clock=None,
        wall_clock=None,
    ) -> None:
        self.store = Path(store)
        self.store.mkdir(parents=True, exist_ok=True)
        self.landmarker = landmarker or UnavailableHandLandmarker()
        self.detector = detector
        self.limits = limits or SafetyLimits()
        self.book = ObjectTrackBook(
            drop_after=self.limits.lost_after_misses,
            ledger=ObjectNameLedger(self.store / "object-names.json"),
        )
        self._clock = clock or __import__("time").monotonic
        self._wall = wall_clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.state = "idle"
        self.stop_reason: str | None = None
        self.selected_track_id: str | None = None
        self.events: list[dict] = []
        self.accepted_frames = 0
        self.started_at: float | None = None
        self.last_accept_at: float | None = None

    def cancel(self) -> dict:
        return self.stop("user", "Stopped watching your hand.")

    def stop(self, reason: str, speech: str) -> dict:
        if self.state != "running":
            self.state = "stopped"
            self.stop_reason = reason
            return self._view(speech, gesture=None, landmarks=[], stop_reason=reason)
        return self._finish(reason, speech)

    def remember(self, name: str, *, consent: bool) -> dict:
        if self.selected_track_id is None:
            return self._view(
                "Nothing is selected, so I did not save a name.",
                gesture="remember",
                blocked="nothing_selected",
                landmarks=[],
            )
        try:
            track = self.book.remember(self.selected_track_id, name, consent=consent)
        except ObjectConsentRequired:
            return self._view(
                "I did not save a name. Naming an object needs an explicit consent flag.",
                gesture="remember",
                blocked="consent_required",
                landmarks=[],
            )
        except ObjectNameError as exc:
            return self._view(str(exc), gesture="remember", blocked="rejected", landmarks=[])
        speech = (
            f"I saved the name {track.durable_name} as {track.durable_id} for tracking id "
            f"{track.track_id}. That name is not inferred again from the category {track.category}."
        )
        event = self._event(
            float(self._clock()),
            "remember",
            gesture="remember",
            speech=speech,
            track=track,
            landmark_count=0,
        )
        self._keep(event, retain=False)
        return self._view(speech, gesture="remember", landmarks=[], track=track, blocked=None)

    def step(
        self,
        image: RgbImage,
        *,
        thermal_c: float | None = None,
        thermal_state: str | None = None,
        battery_percent: int | None = None,
        retain_events: bool = False,
        request_id: str | None = None,
    ) -> dict:
        now = float(self._clock())
        blocked = heat_or_battery(
            self.limits,
            thermal_c=thermal_c,
            thermal_state=thermal_state,
            battery_percent=battery_percent,
        )
        if blocked:
            speech = (
                "Stopped. The glasses are too warm."
                if blocked == "thermal"
                else "Stopped. The phone battery is low."
            )
            if self.state == "running":
                return self._finish(blocked, speech, request_id=request_id)
            return self._view(
                speech,
                gesture=None,
                landmarks=[],
                stop_reason=blocked,
                request_id=request_id,
            )
        if self.state != "running":
            self._begin(now)
        assert self.started_at is not None
        if now - self.started_at >= self.limits.max_session_s:
            return self._finish("timeout", "Stopped. That hand-select window is over.", request_id=request_id)
        if self.accepted_frames >= self.limits.max_frames:
            return self._finish(
                "max_frames",
                "Stopped. That was the frame limit for this session.",
                request_id=request_id,
            )
        if self.last_accept_at is not None and now - self.last_accept_at < self.limits.min_interval_s:
            speech = "Skipping a frame so the glasses don't overheat."
            event = self._event(now, "drop", gesture=None, speech=speech, track=self._selected(), landmark_count=0)
            self._keep(event, retain=retain_events)
            return self._view(
                speech,
                gesture=None,
                landmarks=[],
                dropped=True,
                request_id=request_id,
                retain_events=retain_events,
            )

        self.last_accept_at = now
        self.accepted_frames += 1
        if not getattr(self.landmarker, "available", False):
            speech = _UNAVAILABLE
            event = self._event(now, "frame", gesture="unavailable", speech=speech, track=None, landmark_count=0)
            self._keep(event, retain=retain_events)
            return self._view(
                speech,
                gesture="unavailable",
                landmarks=[],
                blocked="hand_landmarker_unavailable",
                request_id=request_id,
            )

        hands = list(self.landmarker.detect(image))
        detections = list(self.detector.detect(image))
        visible = self.book.update(detections)
        self._drop_selection_if_gone()
        if not hands:
            speech = "I don't see 21 hand landmarks in this frame, so I did not guess a gesture."
            gesture = "none"
            landmarks: list[dict] = []
            track = None
        else:
            pose = hands[0]
            gesture = classify_gesture(pose)
            landmarks = [
                {"x": point.x, "y": point.y, "z": point.z} for point in pose.landmarks
            ]
            speech, track = self._apply(gesture, pose, image, visible)
        event = self._event(
            now,
            "frame",
            gesture=gesture,
            speech=speech,
            track=track if gesture in {"pinch", "fist"} else self._selected() if gesture != "open_palm" else None,
            landmark_count=len(landmarks),
        )
        if gesture == "open_palm":
            event["track_id"] = None
            event["category"] = None
            event["durable_id"] = None
        self._keep(event, retain=retain_events)
        return self._view(
            speech,
            gesture=gesture,
            landmarks=landmarks,
            detections=detections,
            request_id=request_id,
            track=self._selected(),
        )

    def _apply(self, gesture: str, pose, image: RgbImage, visible: list[TrackedObject]):
        if gesture == "open_palm":
            self.selected_track_id = None
            return "Open palm. I cleared the selection. No object identity was saved.", None
        if gesture == "pinch":
            chosen = _object_at_pinch(pose, image, visible)
            if chosen is None:
                self.selected_track_id = None
                return "Pinch. I don't see an object under your fingers.", None
            self.selected_track_id = chosen.track_id
            return (
                f"Pinch. Selected {chosen.category}, tracking id {chosen.track_id}. "
                "That tracking id lasts only for this session and is not a durable identity."
            ), chosen
        if gesture == "fist":
            chosen = self._selected()
            if chosen is None or chosen.misses:
                self.selected_track_id = None
                return "Fist. Nothing is selected.", None
            if chosen.durable_id:
                tail = f" The saved name is {chosen.durable_name}, id {chosen.durable_id}."
            else:
                tail = " No durable identity is stored for it."
            return (
                f"Fist. Confirmed {chosen.category}, tracking id {chosen.track_id}.{tail}"
            ), chosen
        return "I see a hand, but that pose is not a pinch, an open palm, or a fist.", None

    def _begin(self, now: float) -> None:
        self.state = "running"
        self.stop_reason = None
        self.accepted_frames = 0
        self.started_at = now
        self.last_accept_at = None
        self.selected_track_id = None
        self.book = ObjectTrackBook(
            drop_after=self.limits.lost_after_misses,
            ledger=ObjectNameLedger(self.store / "object-names.json"),
        )
        self._keep(
            self._event(
                now,
                "start",
                gesture=None,
                speech="Watching your hand. I will stop within "
                f"{int(self.limits.max_session_s)} seconds.",
                track=None,
                landmark_count=0,
            ),
            retain=False,
        )

    def _finish(self, reason: str, speech: str, *, request_id: str | None = None) -> dict:
        self.state = "stopped"
        self.stop_reason = reason
        event = self._event(
            float(self._clock()) if self.started_at is not None else 0.0,
            "stop",
            gesture=None,
            speech=speech,
            track=self._selected(),
            landmark_count=0,
        )
        event["stop_reason"] = reason
        self._keep(event, retain=False)
        return self._view(speech, gesture=None, landmarks=[], stop_reason=reason, request_id=request_id)

    def _selected(self) -> TrackedObject | None:
        if self.selected_track_id is None:
            return None
        return self.book.tracks.get(self.selected_track_id)

    def _drop_selection_if_gone(self) -> None:
        track = self._selected()
        if track is None:
            self.selected_track_id = None

    def _event(
        self,
        now: float,
        kind: str,
        *,
        gesture: str | None,
        speech: str,
        track: TrackedObject | None,
        landmark_count: int,
    ) -> dict:
        return {
            "t_s": now,
            "wall_time": self._wall(),
            "kind": kind,
            "gesture": gesture,
            "landmark_count": landmark_count,
            "track_id": None if track is None else track.track_id,
            "category": None if track is None else track.category,
            "durable_id": None if track is None else track.durable_id,
            "model_id": None if track is None else track.model_id,
            "speech": speech,
            "evidence": self.evidence(),
        }

    def evidence(self) -> str:
        """``simulated`` for scripted inputs. Never ``physical`` from this process."""
        if getattr(self.landmarker, "simulated", False) or getattr(self.detector, "simulated", False):
            return "simulated"
        if getattr(self.landmarker, "kind", "") == "mediapipe" and getattr(self.landmarker, "available", False):
            return "pc_decoded"
        return "unavailable"

    def _keep(self, event: dict, *, retain: bool) -> None:
        stored = dict(event)
        stored.pop("landmarks", None)
        self.events.append(stored)
        if retain and self.limits.retain_frames:
            path = self.store / "events.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(stored) + "\n")

    def _view(
        self,
        speech: str,
        *,
        gesture: str | None,
        landmarks: list[dict],
        detections: list | None = None,
        dropped: bool = False,
        blocked: str | None = None,
        stop_reason: str | None = None,
        request_id: str | None = None,
        retain_events: bool = False,
        track: TrackedObject | None = None,
    ) -> dict:
        selected = track if track is not None else self._selected()
        if gesture == "open_palm":
            selected = None
        reply = None
        if request_id and speech:
            reply = {
                "method": "voice.reply",
                "params": {"request": request_id, "text": speech, "listen": True},
            }
        return {
            "state": self.state,
            "speech": speech,
            "gesture": gesture,
            "landmark_count": len(landmarks),
            "landmarks": landmarks,
            "expected_landmarks": LANDMARK_COUNT,
            "selected": None if selected is None else _track_dict(selected),
            "detections": detections or [],
            "dropped": dropped,
            "blocked": blocked,
            "stop_reason": stop_reason or self.stop_reason,
            "events": list(self.events),
            "evidence": self.evidence(),
            "voice_reply": reply,
            "search": False,
            "paid_inference": False,
            "assistant_dispatched": False,
            "hardware_verified": False,
            "retained_events": bool(retain_events and self.limits.retain_frames),
        }


def _object_at_pinch(pose, image: RgbImage, visible: list[TrackedObject]) -> TrackedObject | None:
    nx, ny = pinch_point(pose)
    px = nx * image.width
    py = ny * image.height
    containing = []
    for item in visible:
        x, y, width, height = item.bbox
        if x <= px < x + width and y <= py < y + height:
            containing.append(item)
    if not containing:
        return None
    containing.sort(key=lambda item: (item.bbox[2] * item.bbox[3], item.track_id))
    return containing[0]


def _track_dict(track: TrackedObject) -> dict:
    return {
        "track_id": track.track_id,
        "category": track.category,
        "durable_id": track.durable_id,
        "durable_name": track.durable_name,
        "model_id": track.model_id,
        "confidence": track.confidence,
        "bbox": list(track.bbox),
    }
