"""Choose one session track in a crowded frame. Voice is the default.

Numbers and boxes are for a phone or PC preview. Nothing here draws on the
Ray-Ban lenses. A pointing pinch is a 2D image-plane ray, not gaze, depth,
or a command to move a physical object. Track ids die with the session.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cloudos.glasses.vision.detector import _hue_name
from cloudos.glasses.vision.hands import INDEX_TIP, WRIST, UnavailableHandLandmarker, classify_gesture
from cloudos.glasses.vision.images import RgbImage
from cloudos.glasses.vision.objects import ObjectTrackBook
from cloudos.glasses.vision.safety import SafetyLimits, heat_or_battery

PINCH_CONFIRM_FRAMES = 2
_SELECT_NUMBER = re.compile(r"^select object (\d+)$")
_TRACK_SIDE = re.compile(r"^track the (.+) on the (left|center|right)$")
_SWITCH = re.compile(r"^switch to the other (.+)$")
_CONTROL = re.compile(r"^control (?:the |this )?(object|[a-z]+)$")

# 5x7 glyphs. Rows are left to right, 1 means ink.
_GLYPHS = {
    "0": ("111", "101", "101", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "010", "010", "111"),
    "2": ("111", "001", "001", "111", "100", "100", "111"),
    "3": ("111", "001", "001", "111", "001", "001", "111"),
    "4": ("101", "101", "101", "111", "001", "001", "001"),
    "5": ("111", "100", "100", "111", "001", "001", "111"),
    "6": ("111", "100", "100", "111", "101", "101", "111"),
    "7": ("111", "001", "001", "001", "001", "001", "001"),
    "8": ("111", "101", "101", "111", "101", "101", "111"),
    "9": ("111", "101", "101", "111", "001", "001", "111"),
}
_PREVIEW_YELLOW = (255, 220, 0)


@dataclass(frozen=True)
class Candidate:
    number: int
    track_id: str
    label: str
    location: str
    appearance: str | None
    confidence: float
    bbox: tuple[int, int, int, int]
    model_id: str

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "track_id": self.track_id,
            "class": self.label,
            "location": self.location,
            "appearance": self.appearance,
            "confidence": self.confidence,
            "bbox": list(self.bbox),
            "model_id": self.model_id,
            "durable_identity": False,
        }


def horizontal_location(bbox: tuple[int, int, int, int], image_width: int) -> str:
    """Split the frame into left, center, and right thirds."""
    x, _y, width, _h = bbox
    center = x + width / 2.0
    if center < image_width / 3.0:
        return "left"
    if center > image_width * 2.0 / 3.0:
        return "right"
    return "center"


def appearance_hint(image: RgbImage, bbox: tuple[int, int, int, int]) -> str | None:
    """Dominant saturated hue inside the box, or None when the region is dull."""
    x, y, width, height = bbox
    counts: dict[str, int] = {}
    x1 = min(image.width, x + width)
    y1 = min(image.height, y + height)
    for yy in range(max(0, y), y1):
        for xx in range(max(0, x), x1):
            name = _hue_name(*image.pixel(xx, yy))
            if name is not None:
                counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    hue, count = max(counts.items(), key=lambda item: item[1])
    if count < 8:
        return None
    return hue


def label_matches(label: str, query: str) -> bool:
    wanted = query.lower().split()
    have = label.lower().split()
    return bool(wanted) and all(word in have for word in wanted)


def pointing_hits(pose, image: RgbImage, candidates: list[Candidate]) -> list[Candidate]:
    """2D ray from the wrist through the index tip, then across the frame.

    Landmarks are scaled by the image size. There is no camera intrinsic
    matrix and no depth. A box counts when the fingertip is inside it or the
    ray crosses it beyond the fingertip.
    """
    width, height = image.width, image.height
    wrist = pose.landmarks[WRIST]
    tip = pose.landmarks[INDEX_TIP]
    x0, y0 = wrist.x * width, wrist.y * height
    x1, y1 = tip.x * width, tip.y * height
    dx, dy = x1 - x0, y1 - y0
    span = max((dx * dx + dy * dy) ** 0.5, 1e-6)
    step_x, step_y = dx / span, dy / span
    reach = max(width, height)
    hits: list[Candidate] = []
    for candidate in candidates:
        if _contains(candidate.bbox, x1, y1) or _ray_crosses(candidate.bbox, x1, y1, step_x, step_y, reach):
            hits.append(candidate)
    return hits


def render_preview(image: RgbImage, candidates: list[Candidate]) -> RgbImage:
    """Outline each box and stamp its number. This image is not a lens HUD."""
    buf = bytearray(image.rgb)
    for candidate in candidates:
        _outline(buf, image.width, image.height, candidate.bbox, _PREVIEW_YELLOW)
        _stamp(buf, image.width, image.height, candidate.bbox[0] + 2, candidate.bbox[1] + 2, str(candidate.number))
    return RgbImage(image.width, image.height, bytes(buf))


class SelectionSession:
    """Scan, disambiguate, lock one track id, and append a local event log."""

    def __init__(
        self,
        store: Path,
        *,
        detector,
        landmarker=None,
        limits: SafetyLimits | None = None,
        clock=None,
        wall_clock=None,
    ) -> None:
        self.store = Path(store)
        self.store.mkdir(parents=True, exist_ok=True)
        self.detector = detector
        self.landmarker = landmarker or UnavailableHandLandmarker()
        self.limits = limits or SafetyLimits()
        self._clock = clock or __import__("time").monotonic
        self._wall = wall_clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.book = ObjectTrackBook(drop_after=self.limits.lost_after_misses)
        self.state = "idle"
        self.locked_track_id: str | None = None
        self.selection_method: str | None = None
        self.candidates: list[Candidate] = []
        self.numbers: dict[int, str] = {}
        self.pinch_streak = 0
        self.trajectory: list[dict] = []
        self.started_at: float | None = None
        self.last_accept_at: float | None = None
        self.accepted_frames = 0
        self.stop_reason: str | None = None
        self.events: list[dict] = []

    def stop(self, reason: str, speech: str) -> dict:
        self.state = "stopped"
        self.stop_reason = reason
        locked = self.locked_track_id
        self.locked_track_id = None
        self.pinch_streak = 0
        self._log("stop", speech=speech, extra={"stop_reason": reason, "track_id": locked})
        return self._view(speech, stop_reason=reason)

    def handle(
        self,
        utterance: str,
        image: RgbImage | None = None,
        *,
        thermal_c: float | None = None,
        thermal_state: str | None = None,
        battery_percent: int | None = None,
        request_id: str | None = None,
    ) -> dict:
        cleaned = " ".join(re.sub(r"[^a-z0-9' ]+", " ", utterance.lower().replace("’", "'")).split())
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
            if self.state == "following":
                return self.stop(blocked, speech)
            return self._view(speech, stop_reason=blocked, blocked=blocked)

        if _CONTROL.match(cleaned):
            speech = (
                "I can only control software tracking and logging. "
                "I cannot move a physical object, and there is no hardware-control interface."
            )
            self._log("control_refused", speech=speech)
            return self._view(speech, blocked="software_only")

        if cleaned in {"what am i tracking", "what am i tracking now"}:
            return self._status(image, now)

        if cleaned == "log this object":
            return self._log_command()

        if image is None:
            return self._view(
                "I need one authorized frame before I can look. I did not start a camera, "
                "and I did not call Live AI.",
                blocked="no_frame",
            )
        if self.state == "following" and self._over_limit(now):
            if self.accepted_frames >= self.limits.max_frames:
                return self.stop("max_frames", "Stopped. That was the frame limit for this track.")
            return self.stop("timeout", "Stopped. That tracking window is over.")

        if cleaned == "scan objects":
            self._refresh(image)
            speech = _scan_speech(self.candidates)
            self._log("scan", speech=speech)
            return self._view(speech, image=image)

        number = _SELECT_NUMBER.match(cleaned)
        if number:
            return self._select_number(int(number.group(1)), image, now)

        side = _TRACK_SIDE.match(cleaned)
        if side:
            return self._select_query(side.group(1), side.group(2), image, now)

        other = _SWITCH.match(cleaned)
        if other:
            return self._switch(other.group(1), image, now)

        if cleaned in {"point at an object", "point to select"}:
            return self._point(image, now)

        return self._view("Say scan objects, or name a side, or select object and a number.")

    def _status(self, image: RgbImage | None, now: float) -> dict:
        if image is not None and self.state == "following":
            lost = self._advance_lock(image, now)
            if lost is not None:
                return lost
        if self.locked_track_id is None:
            return self._view("I'm not tracking an object.")
        candidate = self._locked_candidate()
        if candidate is None:
            return self._view(
                f"The locked id is {self.locked_track_id}. It is not in the latest scan."
            )
        speech = (
            f"Tracking object {candidate.number}, {candidate.label}, on the {candidate.location}, "
            f"id {candidate.track_id}. That id ends with this session."
        )
        return self._view(speech)

    def _log_command(self) -> dict:
        if self.locked_track_id is None:
            return self._view("Nothing is locked, so I did not write a log.", blocked="nothing_selected")
        candidate = self._locked_candidate()
        speech = f"Logged {self.locked_track_id}."
        self._log(
            "log",
            speech=speech,
            extra={
                "trajectory": list(self.trajectory),
                "selection_method": self.selection_method,
                "label": None if candidate is None else candidate.label,
            },
        )
        return self._view(speech)

    def _over_limit(self, now: float) -> bool:
        if self.accepted_frames >= self.limits.max_frames:
            return True
        return self.started_at is not None and now - self.started_at >= self.limits.max_session_s

    def _select_number(self, number: int, image: RgbImage, now: float) -> dict:
        if not self.numbers:
            speech = "Say scan objects first so the numbers stay still. I did not pick one."
            self._log("clarify", speech=speech)
            return self._view(speech, blocked="needs_scan")
        track_id = self.numbers.get(number)
        if track_id is None:
            speech = f"There is no object {number}. I did not pick one."
            self._log("clarify", speech=speech)
            return self._view(speech, blocked="unknown_number")
        self._refresh(image)
        candidate = self._by_track(track_id)
        if candidate is None:
            speech = f"Object {number} is not in this frame. I did not switch to another object."
            self._log("miss", speech=speech, extra={"requested_track_id": track_id})
            return self._view(speech, image=image, blocked="not_visible")
        return self._lock(candidate, "voice", now, image)

    def _select_query(self, query: str, side: str, image: RgbImage, now: float) -> dict:
        self._refresh(image)
        matches = [
            item for item in self.candidates if label_matches(item.label, query) and item.location == side
        ]
        if len(matches) == 1:
            return self._lock(matches[0], "voice", now, image)
        if not matches:
            speech = f"I don't see a {query.strip()} on the {side}. I did not pick a different object."
        else:
            speech = _clarify(matches, f"{query.strip()} on the {side}")
        self._log("clarify", speech=speech)
        return self._view(speech, image=image, blocked="ambiguous" if matches else "no_match")

    def _switch(self, query: str, image: RgbImage, now: float) -> dict:
        self._refresh(image)
        others = [
            item
            for item in self.candidates
            if label_matches(item.label, query) and item.track_id != self.locked_track_id
        ]
        if len(others) == 1:
            return self._lock(others[0], "voice", now, image)
        if not others:
            speech = f"I don't see another {query.strip()}. I kept the current lock."
            code = "no_match"
        else:
            speech = _clarify(others, query.strip())
            code = "ambiguous"
        self._log("clarify", speech=speech)
        return self._view(speech, image=image, blocked=code)

    def _point(self, image: RgbImage, now: float) -> dict:
        self._refresh(image)
        if not getattr(self.landmarker, "available", False):
            speech = (
                "Pointing needs the optional hand landmarker, and it is not running. "
                "Use scan objects or select object and a number. I did not guess a hand."
            )
            self._log("point_unavailable", speech=speech)
            return self._view(speech, image=image, blocked="hand_landmarker_unavailable")
        hands = list(self.landmarker.detect(image))
        if not hands:
            self.pinch_streak = 0
            speech = "I don't see a hand in this frame, so I did not point. Say scan objects."
            self._log("point_no_hand", speech=speech)
            return self._view(speech, image=image, blocked="no_hand")
        pose = hands[0]
        gesture = classify_gesture(pose)
        hits = pointing_hits(pose, image, self.candidates)
        if gesture != "pinch":
            self.pinch_streak = 0
            speech = _point_speech(hits)
            self._log("point", speech=speech, extra={"hits": [item.number for item in hits], "gesture": gesture})
            return self._view(speech, image=image, pointing={"hits": [item.number for item in hits], "gesture": gesture})
        if len(hits) != 1:
            self.pinch_streak = 0
            speech = "Pinch ignored. Point at one object, or say a number. I did not lock a target."
            self._log("pinch_ignored", speech=speech, extra={"hits": [item.number for item in hits]})
            return self._view(speech, image=image, blocked="ambiguous_point")
        self.pinch_streak += 1
        if self.pinch_streak < PINCH_CONFIRM_FRAMES:
            speech = (
                f"Pinch seen once on object {hits[0].number}. "
                "Hold it for the next frame to confirm. I have not locked a target."
            )
            self._log("pinch_debounce", speech=speech, extra={"track_id": hits[0].track_id})
            return self._view(speech, image=image, blocked="debounce")
        return self._lock(hits[0], "pinch", now, image)

    def _refresh(self, image: RgbImage) -> list[Candidate]:
        detections = list(self.detector.detect(image))
        visible = self.book.update(detections)
        ordered = sorted(visible, key=lambda item: (item.bbox[0], item.track_id))
        candidates: list[Candidate] = []
        for index, track in enumerate(ordered, start=1):
            candidates.append(
                Candidate(
                    number=index,
                    track_id=track.track_id,
                    label=track.category,
                    location=horizontal_location(track.bbox, image.width),
                    appearance=appearance_hint(image, track.bbox),
                    confidence=track.confidence,
                    bbox=track.bbox,
                    model_id=track.model_id,
                )
            )
        self.candidates = candidates
        self.numbers = {item.number: item.track_id for item in candidates}
        return candidates

    def _advance_lock(self, image: RgbImage, now: float) -> dict | None:
        if (
            self.last_accept_at is not None
            and now - self.last_accept_at < self.limits.min_interval_s
        ):
            return self._view("Skipping a frame so the glasses don't overheat.", dropped=True)
        self._refresh(image)
        self.last_accept_at = now
        self.accepted_frames += 1
        track = self.book.tracks.get(self.locked_track_id or "")
        if track is None or track.misses:
            if track is None:
                return self.stop(
                    "lost",
                    "Stopped. I lost that track and I did not switch to a similar object.",
                )
            speech = "I don't see the locked object in this frame. I am not switching."
            self._log("miss", speech=speech)
            return self._view(speech, image=image)
        self.trajectory.append(
            {"t_s": now, "bbox": list(track.bbox), "confidence": track.confidence}
        )
        self._log("follow", speech="Following the locked track.", extra={"bbox": list(track.bbox)})
        return None

    def _lock(self, candidate: Candidate, method: str, now: float, image: RgbImage) -> dict:
        self.locked_track_id = candidate.track_id
        self.selection_method = method
        self.state = "following"
        self.stop_reason = None
        self.pinch_streak = 0
        if self.started_at is None:
            self.started_at = now
        self.last_accept_at = now
        self.accepted_frames += 1
        self.trajectory.append(
            {"t_s": now, "bbox": list(candidate.bbox), "confidence": candidate.confidence}
        )
        speech = (
            f"Locked object {candidate.number}, {candidate.label}, on the {candidate.location}, "
            f"id {candidate.track_id}, by {method}. I will follow only that id."
        )
        self._log("lock", speech=speech, extra={"selection_method": method, "bbox": list(candidate.bbox)})
        return self._view(speech, image=image)

    def _locked_candidate(self) -> Candidate | None:
        return self._by_track(self.locked_track_id)

    def _by_track(self, track_id: str | None) -> Candidate | None:
        if track_id is None:
            return None
        for candidate in self.candidates:
            if candidate.track_id == track_id:
                return candidate
        track = self.book.tracks.get(track_id)
        if track is None:
            return None
        return Candidate(
            number=0,
            track_id=track.track_id,
            label=track.category,
            location="unknown",
            appearance=None,
            confidence=track.confidence,
            bbox=track.bbox,
            model_id=track.model_id,
        )

    def _log(self, kind: str, *, speech: str, extra: dict | None = None) -> None:
        candidate = self._locked_candidate()
        event = {
            "kind": kind,
            "t_s": float(self._clock()) if self.started_at is not None or kind == "scan" else float(self._clock()),
            "wall_time": self._wall(),
            "track_id": self.locked_track_id,
            "label": None if candidate is None else candidate.label,
            "confidence": None if candidate is None else candidate.confidence,
            "selection_method": self.selection_method,
            "speech": speech,
            "session_scoped": True,
            "physical_actuation": False,
        }
        if extra:
            event.update(extra)
        self.events.append(event)
        path = self.store / "selection-log.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")

    def _view(
        self,
        speech: str,
        *,
        image: RgbImage | None = None,
        blocked: str | None = None,
        stop_reason: str | None = None,
        dropped: bool = False,
        pointing: dict | None = None,
    ) -> dict:
        preview = None
        if image is not None:
            drawn = render_preview(image, self.candidates)
            from cloudos.glasses.vision.images import to_ppm
            import base64

            preview = {
                "surface": "phone_or_pc",
                "lens_hud": False,
                "candidates": [item.to_dict() for item in self.candidates],
                "ppm_base64": base64.b64encode(to_ppm(drawn)).decode("ascii"),
            }
        locked = self._locked_candidate()
        return {
            "speech": speech,
            "state": self.state,
            "blocked": blocked,
            "dropped": dropped,
            "stop_reason": stop_reason or self.stop_reason,
            "locked_track_id": self.locked_track_id,
            "selection_method": self.selection_method,
            "candidate": None if locked is None else locked.to_dict(),
            "candidates": [item.to_dict() for item in self.candidates],
            "trajectory": list(self.trajectory),
            "preview": preview,
            "pointing": pointing,
            "projection": "image_plane_2d",
            "intrinsics_calibrated": False,
            "gaze": False,
            "depth": False,
            "physical_actuation": False,
            "live_ai_used": False,
            "live_view_modified": False,
            "paid_inference": False,
            "hardware_verified": False,
            "events": list(self.events),
        }


def _scan_speech(candidates: list[Candidate]) -> str:
    if not candidates:
        return "I don't see a supported object. Nothing was selected."
    parts = []
    for item in candidates:
        hint = f", {item.appearance}" if item.appearance else ""
        parts.append(f"object {item.number}, {item.label}, {item.location}{hint}")
    joined = "; ".join(parts)
    return (
        f"I see {len(candidates)} objects: {joined}. "
        "Numbers are on the phone or PC preview, not in the lenses. "
        "Say select object and a number."
    )


def _clarify(matches: list[Candidate], what: str) -> str:
    bits = [f"object {item.number} on the {item.location}" for item in matches]
    return (
        f"I see {len(matches)} similar {what}: {', '.join(bits)}. "
        "Say select object and a number. I did not pick one."
    )


def _point_speech(hits: list[Candidate]) -> str:
    if len(hits) == 1:
        item = hits[0]
        return (
            f"Pointing at object {item.number}, the {item.label} on the {item.location}. "
            "Pinch on the next frame to confirm. This is a 2D ray, not gaze."
        )
    if not hits:
        return "The pointing ray does not cross a candidate. I did not select."
    numbers = ", ".join(str(item.number) for item in hits)
    return f"The ray crosses objects {numbers}. I did not pick one. Say a number."


def _contains(bbox: tuple[int, int, int, int], x: float, y: float) -> bool:
    left, top, width, height = bbox
    return left <= x < left + width and top <= y < top + height


def _ray_crosses(
    bbox: tuple[int, int, int, int],
    x: float,
    y: float,
    step_x: float,
    step_y: float,
    reach: float,
) -> bool:
    traveled = 0.0
    while traveled < reach:
        if _contains(bbox, x, y):
            return True
        x += step_x * 2.0
        y += step_y * 2.0
        traveled += 2.0
    return False


def _outline(buf: bytearray, width: int, height: int, bbox: tuple[int, int, int, int], color: tuple[int, int, int]) -> None:
    x, y, box_w, box_h = bbox
    pixel = bytes(color)
    for xx in range(x, x + box_w):
        _put(buf, width, height, xx, y, pixel)
        _put(buf, width, height, xx, y + box_h - 1, pixel)
    for yy in range(y, y + box_h):
        _put(buf, width, height, x, yy, pixel)
        _put(buf, width, height, x + box_w - 1, yy, pixel)


def _stamp(buf: bytearray, width: int, height: int, x: int, y: int, text: str) -> None:
    cursor = x
    ink = bytes((255, 255, 255))
    for char in text:
        rows = _GLYPHS.get(char)
        if rows is None:
            continue
        for row_index, row in enumerate(rows):
            for col_index, bit in enumerate(row):
                if bit == "1":
                    _put(buf, width, height, cursor + col_index, y + row_index, ink)
        cursor += 4


def _put(buf: bytearray, width: int, height: int, x: int, y: int, pixel: bytes) -> None:
    if x < 0 or y < 0 or x >= width or y >= height:
        return
    index = (y * width + x) * 3
    buf[index : index + 3] = pixel


def is_selection_utterance(text: str) -> bool:
    cleaned = " ".join(re.sub(r"[^a-z0-9' ]+", " ", text.lower().replace("’", "'")).split())
    if cleaned in {
        "scan objects",
        "what am i tracking",
        "what am i tracking now",
        "log this object",
        "point at an object",
        "point to select",
        "control object",
        "control the object",
        "control this object",
    }:
        return True
    return bool(
        _SELECT_NUMBER.match(cleaned)
        or _TRACK_SIDE.match(cleaned)
        or _SWITCH.match(cleaned)
        or _CONTROL.match(cleaned)
    )
