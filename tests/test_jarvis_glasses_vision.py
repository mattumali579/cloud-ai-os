"""Issue #25: glasses vision pipeline — adapters, detect, track, consent, privacy, safety."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from cloudos.glasses.jarvis import BridgeConfig, classify_command, handle_command
from cloudos.glasses.vision.capture import (
    CaptureAuthorizationError,
    CaptureFrame,
    FixtureCaptureAdapter,
    VisorCameraCaptureAdapter,
    WebcamSimulationAdapter,
)
from cloudos.glasses.vision.detector import BlobClassDetector, Detection
from cloudos.glasses.vision.faces import FaceRegistry, FaceRecognitionDisabled
from cloudos.glasses.vision.pipeline import VisionPipeline, VisionState
from cloudos.glasses.vision.safety import SafetyGate, SafetyStop
from cloudos.glasses.vision.tracker import BoundedTracker, TrackerCancelled


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vision"


def _write_blob_png(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (160, 120)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, (20, 20, 20))
    draw = ImageDraw.Draw(img)
    draw.rectangle((40, 30, 120, 90), fill=color)
    img.save(path)
    return path


@pytest.fixture()
def cup_fixture(tmp_path: Path) -> Path:
    return _write_blob_png(tmp_path / "cup.png", (220, 40, 40))


@pytest.fixture()
def bottle_fixture(tmp_path: Path) -> Path:
    return _write_blob_png(tmp_path / "bottle.png", (40, 80, 220))


@pytest.fixture()
def config(tmp_path: Path) -> BridgeConfig:
    return BridgeConfig(
        visor_url="ws://127.0.0.1:9/rpc",
        search_roots=(tmp_path,),
        phrase="jarvis",
    )


def test_classify_vision_phrases():
    assert classify_command("describe what I'm looking at") == "vision_describe"
    assert classify_command("Jarvis describe what I am looking at") == "vision_describe"
    assert classify_command("track that object") == "vision_track"
    assert classify_command("stop tracking") == "vision_track_stop"
    assert classify_command("who is this enrolled person") == "vision_face_identify"
    assert classify_command("enroll this person as Alice") == "vision_face_enroll"
    assert classify_command("remove enrolled person Alice") == "vision_face_remove"


def test_fixture_capture_requires_authorization(cup_fixture: Path):
    adapter = FixtureCaptureAdapter(cup_fixture, authorized=False)
    with pytest.raises(CaptureAuthorizationError):
        adapter.capture_photo()


def test_fixture_capture_returns_frame_without_retention(cup_fixture: Path, tmp_path: Path):
    adapter = FixtureCaptureAdapter(cup_fixture, authorized=True, retain_dir=None)
    frame = adapter.capture_photo()
    assert isinstance(frame, CaptureFrame)
    assert frame.source == "fixture"
    assert frame.hardware_verified is False
    assert frame.bytes_png
    assert not list(tmp_path.glob("**/*retained*"))


def test_webcam_adapter_is_simulation_only():
    adapter = WebcamSimulationAdapter(authorized=True)
    frame = adapter.capture_photo()
    assert frame.source == "webcam_simulation"
    assert frame.hardware_verified is False


def test_visor_adapter_reports_scope_blocker_without_faking_success():
    class DenyVisor:
        welcome = {"methods": ["glasses.status"], "topics": [], "feeds": []}

        def supports(self, name: str) -> bool:
            return name in self.welcome["methods"]

    adapter = VisorCameraCaptureAdapter(DenyVisor(), authorized=True)
    assert adapter.camera_photo_supported() is False
    with pytest.raises(CaptureAuthorizationError) as exc:
        adapter.capture_photo()
    assert "camera.photo" in str(exc.value).lower() or "scope" in str(exc.value).lower()


def test_detector_returns_label_and_confidence_not_product_identity(cup_fixture: Path):
    detector = BlobClassDetector()
    frame = FixtureCaptureAdapter(cup_fixture, authorized=True).capture_photo()
    detections = detector.detect(frame)
    assert detections
    det = detections[0]
    assert isinstance(det, Detection)
    assert det.label == "cup"
    assert 0.0 < det.confidence <= 1.0
    assert det.identity_kind == "class_label"
    assert det.product_identity is None
    speech = detector.speak(detections)
    assert "cup" in speech.lower()
    assert "exact product" not in speech.lower() or "class" in speech.lower()


def test_tracker_lifecycle_cancel_and_backpressure(cup_fixture: Path, bottle_fixture: Path):
    detector = BlobClassDetector()
    tracker = BoundedTracker(detector, max_frames=4, max_fps=2.0)
    frames = [
        FixtureCaptureAdapter(cup_fixture, authorized=True).capture_photo(),
        FixtureCaptureAdapter(cup_fixture, authorized=True).capture_photo(),
        FixtureCaptureAdapter(bottle_fixture, authorized=True).capture_photo(),
    ]
    updates = list(tracker.run(frames))
    assert updates
    assert tracker.state in {"stopped", "completed"}
    tracker2 = BoundedTracker(detector, max_frames=10, max_fps=30.0)
    tracker2.cancel()
    with pytest.raises(TrackerCancelled):
        list(tracker2.run(frames))
    # Backpressure: queue drops oldest when overfilled
    tracker3 = BoundedTracker(detector, max_frames=8, max_fps=30.0, queue_size=1)
    dropped = tracker3.enqueue_with_backpressure(frames)
    assert dropped >= 1


def test_face_recognition_off_by_default_and_unknown(tmp_path: Path, cup_fixture: Path):
    registry = FaceRegistry(tmp_path / "faces", enabled=False)
    with pytest.raises(FaceRecognitionDisabled):
        registry.identify(FixtureCaptureAdapter(cup_fixture, authorized=True).capture_photo())
    registry_on = FaceRegistry(tmp_path / "faces2", enabled=True)
    result = registry_on.identify(FixtureCaptureAdapter(cup_fixture, authorized=True).capture_photo())
    assert result.status == "unknown"
    assert result.spoken_as_fact is False
    assert "unknown" in result.speech.lower()


def test_face_enroll_consent_only_encrypted_and_removable(tmp_path: Path, cup_fixture: Path):
    registry = FaceRegistry(tmp_path / "faces", enabled=True)
    frame = FixtureCaptureAdapter(cup_fixture, authorized=True).capture_photo()
    with pytest.raises(PermissionError):
        registry.enroll(frame, display_name="Alice", consent=False)
    enrolled = registry.enroll(frame, display_name="Alice", consent=True)
    assert enrolled.display_name == "Alice"
    store_files = list((tmp_path / "faces").rglob("*"))
    assert store_files
    # raw embedding must not sit in plaintext json next to names without encryption marker
    raw = (tmp_path / "faces" / "index.json").read_text(encoding="utf-8") if (tmp_path / "faces" / "index.json").exists() else ""
    assert "embedding_plain" not in raw
    hit = registry.identify(frame)
    assert hit.status == "enrolled_match"
    assert hit.display_name == "Alice"
    assert hit.spoken_as_fact is False  # probabilistic; never stated as hard fact
    assert "likely" in hit.speech.lower() or "matches enrolled" in hit.speech.lower()
    removed = registry.remove("Alice")
    assert removed is True
    again = registry.identify(frame)
    assert again.status == "unknown"


def test_safety_stops_on_thermal_and_low_battery():
    gate = SafetyGate(min_battery_percent=15, max_temperature_c=45.0)
    gate.check({"battery": 80, "temperature_c": 32})
    with pytest.raises(SafetyStop) as hot:
        gate.check({"battery": 80, "temperature_c": 48})
    assert "heat" in str(hot.value).lower() or "thermal" in str(hot.value).lower()
    with pytest.raises(SafetyStop) as bat:
        gate.check({"battery": 10, "temperature_c": 30})
    assert "battery" in str(bat.value).lower()


def test_pipeline_describe_uses_authorized_capture(cup_fixture: Path, tmp_path: Path):
    pipe = VisionPipeline(
        capture=FixtureCaptureAdapter(cup_fixture, authorized=True),
        detector=BlobClassDetector(),
        faces=FaceRegistry(tmp_path / "faces", enabled=False),
        safety=SafetyGate(),
    )
    result = pipe.describe()
    assert result.state == VisionState.COMPLETE
    assert "cup" in result.speech.lower()
    assert result.hardware_verified is False
    assert pipe.visible_state()["retention"] == "off"


def test_pipeline_blocks_unauthorized_and_thermal(cup_fixture: Path, tmp_path: Path):
    pipe = VisionPipeline(
        capture=FixtureCaptureAdapter(cup_fixture, authorized=False),
        detector=BlobClassDetector(),
        faces=FaceRegistry(tmp_path / "faces", enabled=False),
        safety=SafetyGate(),
    )
    result = pipe.describe()
    assert result.state == VisionState.BLOCKED
    assert "authoriz" in result.speech.lower() or "permission" in result.speech.lower()

    pipe2 = VisionPipeline(
        capture=FixtureCaptureAdapter(cup_fixture, authorized=True),
        detector=BlobClassDetector(),
        faces=FaceRegistry(tmp_path / "faces2", enabled=False),
        safety=SafetyGate(max_temperature_c=40.0),
        glasses_status={"battery": 90, "temperature_c": 55},
    )
    result2 = pipe2.describe()
    assert result2.state == VisionState.STOPPED_SAFETY
    assert "heat" in result2.speech.lower() or "thermal" in result2.speech.lower() or "hot" in result2.speech.lower()


def test_pipeline_track_and_cancel(cup_fixture: Path, tmp_path: Path):
    pipe = VisionPipeline(
        capture=FixtureCaptureAdapter(cup_fixture, authorized=True),
        detector=BlobClassDetector(),
        faces=FaceRegistry(tmp_path / "faces", enabled=False),
        safety=SafetyGate(),
    )
    started = pipe.start_track(max_frames=3)
    assert started.state in {VisionState.TRACKING, VisionState.COMPLETE}
    stopped = pipe.stop_track()
    assert stopped.state == VisionState.STOPPED
    assert pipe.visible_state()["tracking"] is False


def test_handle_command_vision_phrases_do_not_fall_through_to_search(config: BridgeConfig, cup_fixture: Path, tmp_path: Path, monkeypatch):
    from cloudos.glasses import vision as vision_mod

    pipe = VisionPipeline(
        capture=FixtureCaptureAdapter(cup_fixture, authorized=True),
        detector=BlobClassDetector(),
        faces=FaceRegistry(tmp_path / "faces", enabled=False),
        safety=SafetyGate(),
    )
    monkeypatch.setattr(vision_mod, "get_runtime_pipeline", lambda: pipe)
    reply = handle_command("Jarvis describe what I'm looking at", config)
    assert "cup" in reply.lower()
    assert "google" not in reply.lower()
    track = handle_command("Jarvis track that object", config)
    assert "track" in track.lower() or "cup" in track.lower()
    stop = handle_command("Jarvis stop tracking", config)
    assert "stop" in stop.lower()
    who = handle_command("Jarvis who is this enrolled person", config)
    assert "off" in who.lower() or "disabled" in who.lower() or "unknown" in who.lower()


def test_existing_desktop_classifications_regress():
    assert classify_command("Jarvis open chrome") == "open"
    assert classify_command("Jarvis close chrome") == "close"
    assert classify_command("Jarvis search the web for cats") == "web"
    assert classify_command("Jarvis status") == "status"


def test_visor_live_session_bounds_documented_in_adapter():
    meta = VisorCameraCaptureAdapter.session_policy()
    assert meta["default_mode"] == "single_shot"
    assert meta["max_fps"] <= 7
    assert meta["hidden_streaming"] is False
    assert meta["retain_frames_default"] is False
