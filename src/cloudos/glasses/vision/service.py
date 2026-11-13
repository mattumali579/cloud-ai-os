"""Route glasses vision phrases. Desktop commands and web search are left alone."""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path

from cloudos.glasses.vision.capture import CapturedFrame
from cloudos.glasses.vision.detector import CoarseRegionDetector, Detection
from cloudos.glasses.vision.faces import (
    ConsentRequired,
    FaceBox,
    FaceGallery,
    GalleryError,
    UnavailableFaceFinder,
)
from cloudos.glasses.vision.images import RgbImage, to_ppm
from cloudos.glasses.vision.safety import SafetyLimits, heat_or_battery
from cloudos.glasses.vision.tracker import TrackingSession

_SPACE = re.compile(r"[^a-z0-9' ]+")
_ENROLL = re.compile(
    r"\benroll (?:this person|them) as ([A-Za-z][A-Za-z .'-]{0,40})",
    re.IGNORECASE,
)
_FORGET = re.compile(
    r"\b(?:forget|delete|remove) (?:the )?enrolled person ([A-Za-z][A-Za-z .'-]{0,40})",
    re.IGNORECASE,
)

DESCRIBE_PHRASES = (
    "describe what i'm looking at",
    "describe what i am looking at",
    "what am i looking at",
    "what i'm looking at",
)
TRACK_PHRASES = ("track that object", "track this object", "start tracking")
WHO_EXACT = {"who is this", "who is this enrolled person"}


def normalize(text: str) -> str:
    return " ".join(_SPACE.sub(" ", text.lower().replace("’", "'")).split())


def is_vision_utterance(text: str) -> bool:
    return classify(text) is not None


def classify(text: str) -> str | None:
    """Return the vision intent, or None when Jarvis should keep its own route."""
    cleaned = normalize(text)
    if not cleaned:
        return None
    if cleaned in {"stop tracking", "stop the tracking", "cancel tracking"}:
        return "stop_tracking"
    if _ENROLL.search(cleaned):
        return "enroll"
    if _FORGET.search(cleaned):
        return "forget"
    if cleaned in WHO_EXACT:
        return "who"
    if any(phrase in cleaned for phrase in TRACK_PHRASES):
        return "track"
    if any(phrase in cleaned for phrase in DESCRIBE_PHRASES):
        return "describe"
    return None


class VisionService:
    """One local session: capture result in, speech out. No network calls."""

    def __init__(
        self,
        store: Path,
        *,
        detector: CoarseRegionDetector | None = None,
        face_finder: UnavailableFaceFinder | None = None,
        limits: SafetyLimits | None = None,
        clock=None,
    ) -> None:
        self.store = Path(store)
        self.store.mkdir(parents=True, exist_ok=True)
        self.detector = detector or CoarseRegionDetector()
        self.face_finder = face_finder or UnavailableFaceFinder()
        self.limits = limits or SafetyLimits()
        self.gallery = FaceGallery(self.store / "faces")
        self.tracker = TrackingSession(self.limits)
        self._clock = clock or time.monotonic

    def handle(
        self,
        utterance: str,
        frame: CapturedFrame | RgbImage | None = None,
        *,
        consent: bool = False,
        face_crop: bool = False,
        thermal_c: float | None = None,
        thermal_state: str | None = None,
        battery_percent: int | None = None,
        retain_frames: bool = False,
        request_id: str | None = None,
    ) -> dict:
        intent = classify(utterance)
        if intent is None:
            return {
                "handled": False,
                "intent": None,
                "speech": "",
                "search": False,
                "route": "passthrough",
                "hardware_verified": False,
            }
        image = _image_of(frame)
        blocked = heat_or_battery(
            self.limits,
            thermal_c=thermal_c,
            thermal_state=thermal_state,
            battery_percent=battery_percent,
        )
        if blocked and intent in {"describe", "track", "who", "enroll"}:
            if intent == "track" and self.tracker.state == "running":
                speech = (
                    "Stopped. The glasses are too warm."
                    if blocked == "thermal"
                    else "Stopped. The phone battery is low."
                )
                self.tracker.cancel()
                self.tracker.stop_reason = blocked
            else:
                speech = (
                    "Stopped. The glasses are too warm."
                    if blocked == "thermal"
                    else "Stopped. The phone battery is low."
                )
            return self._result(intent, speech, stop_reason=blocked, request_id=request_id)

        if intent == "stop_tracking":
            update = self.tracker.cancel()
            return self._result(intent, update.speech, stop_reason=update.stop_reason, request_id=request_id)
        if intent == "describe":
            return self._describe(image, retain_frames, request_id)
        if intent == "track":
            return self._track(image, retain_frames, thermal_c, thermal_state, battery_percent, request_id)
        if intent == "who":
            return self._who(image, face_crop, request_id)
        if intent == "enroll":
            return self._enroll(utterance, image, consent, request_id)
        if intent == "forget":
            return self._forget(utterance, request_id)
        return self._result(intent, "I didn't take a camera action.", request_id=request_id)

    def _describe(self, image: RgbImage | None, retain_frames: bool, request_id: str | None) -> dict:
        if image is None:
            return self._result(
                "describe",
                "I need one authorized photo from the glasses before I can describe the view. I did not search the web.",
                blocked="no_frame",
                request_id=request_id,
            )
        retained = self._retain(image, retain_frames)
        detections = self.detector.detect(image)
        speech = _describe_speech(detections)
        return self._result(
            "describe",
            speech,
            detections=detections,
            retained_frame=retained,
            request_id=request_id,
        )

    def _track(
        self,
        image: RgbImage | None,
        retain_frames: bool,
        thermal_c: float | None,
        thermal_state: str | None,
        battery_percent: int | None,
        request_id: str | None,
    ) -> dict:
        if image is None:
            return self._result(
                "track",
                "Say that again with a camera frame. I have not started a live stream, "
                "because Visor 3.0.4's camera.live feed is encoded video and this "
                "process cannot decode it.",
                blocked="no_decoded_live_feed",
                request_id=request_id,
            )
        self._retain(image, retain_frames)
        detections = self.detector.detect(image)
        now = float(self._clock())
        update = self.tracker.push(
            detections,
            now=now,
            image_width=image.width,
            image_height=image.height,
            thermal_c=thermal_c,
            thermal_state=thermal_state,
            battery_percent=battery_percent,
        )
        return self._result(
            "track",
            update.speech,
            detections=detections,
            stop_reason=update.stop_reason,
            track={
                "state": update.state,
                "label": update.label,
                "track_id": update.track_id,
                "dropped": update.dropped,
                "accepted_frames": update.accepted_frames,
            },
            request_id=request_id,
        )

    def _who(self, image: RgbImage | None, face_crop: bool, request_id: str | None) -> dict:
        if image is None:
            return self._result(
                "who",
                "I need an authorized photo before I can compare it with enrolled people.",
                blocked="no_frame",
                request_id=request_id,
            )
        boxes = list(self.face_finder.find(image))
        if not boxes and not face_crop:
            return self._result(
                "who",
                "I can't see a face with the offline detector on this machine. "
                "I did not guess an identity.",
                blocked="face_detector_unavailable",
                request_id=request_id,
                face_boxes=[],
            )
        crop = image if face_crop or not boxes else _crop(image, boxes[0])
        identity = self.gallery.identify(crop)
        return self._result(
            "who",
            identity.speech,
            identity={
                "status": identity.status,
                "name": identity.name,
                "score": identity.score,
                "certain": False,
            },
            face_boxes=[_box_dict(box) for box in boxes],
            request_id=request_id,
        )

    def _enroll(self, utterance: str, image: RgbImage | None, consent: bool, request_id: str | None) -> dict:
        match = _ENROLL.search(" ".join(utterance.split()))
        if match is None or image is None:
            return self._result(
                "enroll",
                "Say enroll this person as their name, and include a photo they consented to.",
                blocked="needs_name_and_photo",
                request_id=request_id,
            )
        try:
            identity = self.gallery.enroll(match.group(1), image, consent=consent)
        except ConsentRequired:
            return self._result(
                "enroll",
                "I did not save anything. Enrollment needs an explicit consent flag.",
                blocked="consent_required",
                request_id=request_id,
            )
        except GalleryError as exc:
            return self._result("enroll", str(exc), blocked="rejected", request_id=request_id)
        return self._result(
            "enroll",
            identity.speech,
            identity={"status": "enrolled", "name": identity.name, "certain": False},
            request_id=request_id,
        )

    def _forget(self, utterance: str, request_id: str | None) -> dict:
        match = _FORGET.search(" ".join(utterance.split()))
        if match is None:
            return self._result(
                "forget",
                "Say delete enrolled person and their name.",
                blocked="needs_name",
                request_id=request_id,
            )
        try:
            removed = self.gallery.delete(match.group(1))
        except GalleryError as exc:
            return self._result("forget", str(exc), blocked="rejected", request_id=request_id)
        speech = "Removed that enrollment." if removed else "That person was not enrolled."
        return self._result("forget", speech, request_id=request_id)

    def _retain(self, image: RgbImage, retain_frames: bool) -> str | None:
        if not (retain_frames and self.limits.retain_frames):
            return None
        folder = self.store / "frames"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{uuid.uuid4().hex}.ppm"
        path.write_bytes(to_ppm(image))
        return str(path)

    def _result(
        self,
        intent: str,
        speech: str,
        *,
        detections: list[Detection] | None = None,
        track: dict | None = None,
        identity: dict | None = None,
        stop_reason: str | None = None,
        blocked: str | None = None,
        retained_frame: str | None = None,
        face_boxes: list | None = None,
        request_id: str | None = None,
    ) -> dict:
        reply = None
        if request_id:
            reply = {
                "method": "voice.reply",
                "params": {"request": request_id, "text": speech, "listen": True},
            }
        return {
            "handled": True,
            "intent": intent,
            "speech": speech,
            "search": False,
            "route": "glasses_vision",
            "hardware_verified": False,
            "follow_up_hardware_verified": False,
            "listen_requested": True,
            "disclaimer": "This is a visual description, not a safety inspection.",
            "detections": [_detection_dict(item) for item in detections or []],
            "track": track,
            "identity": identity,
            "stop_reason": stop_reason,
            "blocked": blocked,
            "retained_frame": retained_frame,
            "face_boxes": face_boxes or [],
            "voice_reply": reply,
            "paid_inference": False,
        }


_SERVICES: dict[tuple, VisionService] = {}


def get_service(store: Path, limits: SafetyLimits | None = None) -> VisionService:
    """Reuse one in-process session per store so a short track can span calls."""
    chosen = limits or SafetyLimits()
    key = (str(Path(store).resolve()), chosen)
    service = _SERVICES.get(key)
    if service is None:
        service = VisionService(store, limits=chosen)
        _SERVICES[key] = service
    return service


def reset_services() -> None:
    _SERVICES.clear()


def default_store() -> Path:
    configured = os.environ.get("GLASSES_VISION_STORE", "").strip()
    if configured:
        return Path(configured)
    return Path.home() / ".local" / "share" / "cloud-ai-os" / "glasses-vision"


def _image_of(frame: CapturedFrame | RgbImage | None) -> RgbImage | None:
    if frame is None:
        return None
    if isinstance(frame, CapturedFrame):
        return frame.image
    return frame


def _describe_speech(detections: list[Detection]) -> str:
    if not detections:
        return (
            "I don't see a distinct colored object. "
            "This look cannot name a product model, and it is not a safety check."
        )
    lead = detections[0]
    extra = ""
    if len(detections) > 1:
        extra = f" I also see {len(detections) - 1} other colored region{'s' if len(detections) > 2 else ''}."
    percent = int(round(lead.confidence * 100))
    return (
        f"I see a {lead.label}, about {percent} percent on a coarse color match.{extra} "
        "That is not a specific product model, and it is not a safety check."
    )


def _detection_dict(item: Detection) -> dict:
    return {
        "label": item.label,
        "confidence": item.confidence,
        "bbox": list(item.bbox),
        "model_id": item.model_id,
        "product_model": False,
    }


def _box_dict(box: FaceBox) -> dict:
    return {"x": box.x, "y": box.y, "width": box.width, "height": box.height}


def _crop(image: RgbImage, box: FaceBox) -> RgbImage:
    from cloudos.glasses.vision.images import RgbImage as Image

    x0 = max(0, box.x)
    y0 = max(0, box.y)
    x1 = min(image.width, box.x + box.width)
    y1 = min(image.height, box.y + box.height)
    if x1 <= x0 or y1 <= y0:
        return image
    buf = bytearray()
    for y in range(y0, y1):
        start = (y * image.width + x0) * 3
        buf.extend(image.rgb[start : start + (x1 - x0) * 3])
    return Image(x1 - x0, y1 - y0, bytes(buf))
