"""Glasses vision pipeline. No hardware, no network, no paid model.

Run: python -m pytest tests/test_glasses_vision.py -q
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from cloudos.glasses.vision.capability import visor_304_camera_capability
from cloudos.glasses.vision.capture import (
    CaptureError,
    FrameSequenceAdapter,
    LiveFeedAdapter,
    PhotoFileAdapter,
    ScriptedVisor,
    VisorStillAdapter,
)
from cloudos.glasses.vision.faces import FaceBox, FaceGallery
from cloudos.glasses.vision.images import paint_rect, parse_ppm, solid, to_ppm
from cloudos.glasses.vision.safety import SafetyLimits
from cloudos.glasses.vision.service import VisionService, classify, is_vision_utterance


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class OneFace:
    def find(self, image):
        return [FaceBox(0, 0, image.width, image.height)]


def _scene(x: int):
    base = solid(80, 60, (140, 140, 140))
    return paint_rect(base, x, 16, 20, 20, (220, 30, 30))


def _service(tmp_path: Path, **kwargs) -> VisionService:
    return VisionService(tmp_path / "store", **kwargs)


def test_capability_names_the_visor_304_frame_paths():
    report = visor_304_camera_capability()
    assert report["visor_version"] == "3.0.4"
    assert report["vdk_version"] == "0.1.1"
    assert report["hardware_verified"] is False
    assert report["frames_exposed"] is True
    names = {item["api"] for item in report["paths"]}
    assert "camera.photo" in names
    assert "camera.live.photo" in names
    assert "camera.live" in names
    assert "com.macedonga.visor.action.LIVE_FRAMES" in names
    assert report["tailscale"]["live_failure_reason"] == "vpn"
    assert report["tailscale"]["jarvis_reachability"] == "NOT VERIFIED"
    assert "Meta Live AI" in report["not_used"]


def test_ppm_roundtrip_and_coarse_labels(tmp_path: Path):
    image = _scene(8)
    blue = paint_rect(image, 50, 16, 20, 20, (30, 40, 220))
    restored = parse_ppm(to_ppm(blue))
    detections = _service(tmp_path).detector.detect(restored)
    labels = {item.label for item in detections}
    assert labels == {"red object", "blue object"}
    assert all(item.product_model is False for item in detections)
    assert all(item.confidence <= 0.93 for item in detections)


def test_describe_speech_is_not_a_search_or_a_product_id(tmp_path: Path):
    result = _service(tmp_path).handle("describe what I'm looking at", _scene(8))
    assert result["handled"] is True
    assert result["search"] is False
    assert result["route"] == "glasses_vision"
    assert result["hardware_verified"] is False
    assert result["paid_inference"] is False
    assert "red object" in result["speech"]
    assert "product model" in result["speech"]
    assert "safety" in result["speech"]
    assert "google" not in result["speech"].lower()
    assert result["detections"][0]["product_model"] is False
    assert not (tmp_path / "store" / "frames").exists()


def test_desktop_commands_are_not_captured_by_vision():
    for utterance in (
        "Jarvis, open Chrome.",
        "Search LSU construction management admissions",
        "Open Notepad, type GLASSES-JARVIS-TEST, save to Desktop as jarvis-glasses-test.txt.",
        "now click that",
        "Stop Jarvis",
        "what's five plus five",
    ):
        assert classify(utterance) is None
        assert is_vision_utterance(utterance) is False


def test_phone_task_does_not_send_vision_phrases_to_a_model(monkeypatch):
    from cloudos.api import phone

    def fail(*_args, **_kwargs):
        raise AssertionError("vision phrase reached a model")

    monkeypatch.setattr(phone.claude_cli, "generate", fail)
    monkeypatch.setattr(phone.codex_cli, "generate", fail)
    result = phone.execute_phone_task("describe what I'm looking at", "auto")
    assert result["agent"] == "glasses-vision"
    assert result["search"] is False
    assert "web search" in result["output"]


def test_authorized_file_and_refused_file(tmp_path: Path):
    path = tmp_path / "look.ppm"
    path.write_bytes(to_ppm(_scene(8)))
    frame = PhotoFileAdapter().load(path, authorized=True)
    assert frame.hardware_verified is False
    assert frame.authorized is True
    assert frame.image.width == 80
    with pytest.raises(CaptureError) as refused:
        PhotoFileAdapter().load(path, authorized=False)
    assert refused.value.code == "unauthorized"


def test_visor_still_decodes_base64_and_blocks_phone_uri():
    image = _scene(8)
    client = ScriptedVisor(
        {
            "camera.live.photo": {
                "mime": "image/x-portable-pixmap",
                "data": base64.b64encode(to_ppm(image)).decode("ascii"),
            }
        },
        supported=("camera.live.photo", "camera.photo"),
    )
    adapter = VisorStillAdapter(client, device_token="device-secret")
    frame = adapter.capture(presented_token="device-secret")
    assert frame.source == "visor-vdk:camera.live.photo"
    assert frame.hardware_verified is False
    assert frame.simulated is True
    assert client.calls == [("camera.live.photo", {})]

    denied = ScriptedVisor({}, supported=("camera.live.photo",))
    with pytest.raises(CaptureError) as auth:
        VisorStillAdapter(denied, device_token="device-secret").capture(presented_token="nope")
    assert auth.value.code == "auth"
    assert denied.calls == []

    gallery = ScriptedVisor(
        {"camera.photo": {"uri": "content://media/external/images/media/7", "id": "cap", "type": "photo"}},
        supported=("camera.photo",),
    )
    with pytest.raises(CaptureError) as blocked:
        VisorStillAdapter(gallery, device_token="device-secret").capture(presented_token="device-secret")
    assert blocked.value.code == "blocked"
    assert "content://" in blocked.value.message

    heic = ScriptedVisor(
        {"camera.live.photo": {"mime": "image/heic", "data": base64.b64encode(b"not-a-photo").decode("ascii")}},
        supported=("camera.live.photo",),
    )
    with pytest.raises(CaptureError) as undecoded:
        VisorStillAdapter(heic, device_token="device-secret").capture(presented_token="device-secret")
    assert "HEIC" in undecoded.value.message


def test_live_feed_is_blocked_without_a_decoder():
    with pytest.raises(CaptureError) as blocked:
        LiveFeedAdapter().open_session(frame_rate=2)
    assert blocked.value.code == "blocked"
    assert "HEVC" in blocked.value.message
    with pytest.raises(CaptureError):
        LiveFeedAdapter().open_session(frame_rate=12)


def test_frame_sequence_adapter_ends():
    adapter = FrameSequenceAdapter([_scene(4), _scene(30)])
    assert adapter.next_frame() is not None
    assert adapter.next_frame() is not None
    assert adapter.next_frame() is None


def test_track_moves_drops_and_stops(tmp_path: Path):
    clock = Clock()
    limits = SafetyLimits(max_session_s=20, max_fps=2, max_frames=10, lost_after_misses=3)
    service = _service(tmp_path, limits=limits, clock=clock)
    first = service.handle("track that object", _scene(4))
    assert first["track"]["state"] == "running"
    assert first["track"]["dropped"] is False
    assert "red object" in first["speech"]
    assert first["search"] is False

    clock.now = 0.1
    dropped = service.handle("track that object", _scene(40))
    assert dropped["track"]["dropped"] is True
    assert "Skipping" in dropped["speech"]

    clock.now = 0.6
    moved = service.handle("track that object", _scene(40))
    assert moved["track"]["dropped"] is False
    assert "right" in moved["speech"]
    assert moved["track"]["accepted_frames"] == 2

    stopped = service.handle("stop tracking")
    assert stopped["stop_reason"] == "user"
    assert stopped["speech"] == "Stopped tracking."

    clock.now = 1.2
    restarted = service.handle("track that object", _scene(4))
    assert restarted["track"]["state"] == "running"
    assert restarted["track"]["accepted_frames"] == 1


def test_track_stops_for_heat_battery_timeout_and_loss(tmp_path: Path):
    hot = _service(tmp_path / "hot")
    hot.tracker.start(0.0)
    result = hot.handle("track that object", _scene(4), thermal_state="hot")
    assert result["stop_reason"] == "thermal"
    assert "warm" in result["speech"]

    battery = _service(tmp_path / "battery")
    result = battery.handle("describe what I'm looking at", _scene(4), battery_percent=5)
    assert result["stop_reason"] == "battery"
    assert result["detections"] == []

    clock = Clock()
    timed = _service(
        tmp_path / "time",
        limits=SafetyLimits(max_session_s=1, max_fps=2, max_frames=10),
        clock=clock,
    )
    timed.handle("track that object", _scene(4))
    clock.now = 1.0
    over = timed.handle("track that object", _scene(8))
    assert over["stop_reason"] == "timeout"

    clock = Clock()
    lost = _service(
        tmp_path / "lost",
        limits=SafetyLimits(max_session_s=30, max_fps=2, max_frames=10, lost_after_misses=2),
        clock=clock,
    )
    blank = solid(80, 60, (140, 140, 140))
    lost.handle("track that object", blank)
    clock.now = 0.6
    gone = lost.handle("track that object", blank)
    assert gone["stop_reason"] == "lost"


def test_track_without_a_decoded_feed_does_not_invent_frames(tmp_path: Path):
    result = _service(tmp_path).handle("track that object")
    assert result["blocked"] == "no_decoded_live_feed"
    assert result["hardware_verified"] is False
    assert "encoded video" in result["speech"]


def test_prerecorded_sequence_can_be_cancelled_by_the_caller(tmp_path: Path):
    clock = Clock()
    service = _service(tmp_path, clock=clock)
    frames = [_scene(4), _scene(20), _scene(40)]
    updates = []
    for index, frame in enumerate(frames):
        clock.now = index * 0.6
        if index == 2:
            updates.append(service.handle("stop tracking"))
            break
        updates.append(service.handle("track that object", frame))
    assert updates[-1]["stop_reason"] == "user"
    assert service.tracker.state == "stopped"


def test_face_consent_unknown_enrolled_delete_and_restart(tmp_path: Path):
    ada = solid(32, 32, (200, 40, 40))
    stranger = solid(32, 32, (40, 40, 200))
    store = tmp_path / "faces"
    service = VisionService(store, face_finder=OneFace())

    refused = service.handle("enroll this person as Ada", ada, consent=False)
    assert refused["blocked"] == "consent_required"
    assert service.gallery.names() == []
    assert "Ada" not in refused["speech"]

    saved = service.handle("enroll this person as Ada", ada, consent=True)
    assert saved["identity"]["certain"] is False
    assert "not a fact" in saved["speech"] or "resemblance" in saved["speech"]

    known = service.handle("who is this enrolled person", ada, face_crop=True)
    assert known["identity"]["status"] == "resembles"
    assert known["identity"]["name"] == "Ada"
    assert known["identity"]["certain"] is False
    assert "not certain" in known["speech"]
    assert "this is ada" not in known["speech"].lower()

    unknown = service.handle("who is this", stranger, face_crop=True)
    assert unknown["identity"]["status"] == "unknown"
    assert unknown["identity"]["name"] is None
    assert "Unknown" in unknown["speech"]
    assert "Ada" not in unknown["speech"]

    no_detector = VisionService(store)
    blocked = no_detector.handle("who is this", ada)
    assert blocked["blocked"] == "face_detector_unavailable"
    assert "Ada" not in blocked["speech"]

    removed = service.handle("delete enrolled person Ada")
    assert "Removed" in removed["speech"]
    assert service.gallery.names() == []
    audit = service.gallery.privacy_audit()
    assert audit["raw_images"] == []
    assert audit["uploads"] is False
    assert audit["people"] == 0

    again = service.handle("enroll this person as Ada", ada, consent=True)
    assert again["identity"]["name"] == "Ada"
    restarted = FaceGallery(store / "faces")
    identity = restarted.identify(ada)
    assert identity.status == "resembles"
    assert identity.certain is False
    assert json.loads((store / "faces" / "enrollments.json").read_text())["people"]["ada"]["embedding"]


def test_frames_stay_unretained_unless_policy_and_call_both_ask(tmp_path: Path):
    service = _service(tmp_path)
    service.handle("describe what I am looking at", _scene(4), retain_frames=True)
    assert not (tmp_path / "store" / "frames").exists()

    opted = _service(tmp_path / "opt", limits=SafetyLimits(retain_frames=True))
    result = opted.handle("describe what I'm looking at", _scene(4), retain_frames=True)
    assert result["retained_frame"]
    assert Path(result["retained_frame"]).is_file()


def test_voice_reply_asks_to_keep_listening_without_claiming_it_worked(tmp_path: Path):
    result = _service(tmp_path).handle(
        "what am I looking at",
        _scene(4),
        request_id="req-1",
    )
    assert result["voice_reply"] == {
        "method": "voice.reply",
        "params": {"request": "req-1", "text": result["speech"], "listen": True},
    }
    assert result["follow_up_hardware_verified"] is False
    assert result["listen_requested"] is True


def test_api_requires_bearer_and_describes_without_saving_frames(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from cloudos import config
    from cloudos.api.app import app
    from cloudos.glasses.vision.service import reset_services

    monkeypatch.setenv("AGENT_API_TOKEN", "test-token-abc123")
    monkeypatch.setenv("GLASSES_VISION_STORE", str(tmp_path / "api-store"))
    monkeypatch.delenv("GLASSES_VISION_DEVICE_TOKEN", raising=False)
    monkeypatch.delenv("GLASSES_VISION_RETAIN_FRAMES", raising=False)
    config.reset_settings_cache()
    reset_services()
    image = base64.b64encode(to_ppm(_scene(6))).decode("ascii")
    with TestClient(app, raise_server_exceptions=False) as client:
        missing = client.post(
            "/v1/glasses/vision",
            json={"utterance": "describe what I'm looking at", "image_base64": image},
        )
        assert missing.status_code == 401

        described = client.post(
            "/v1/glasses/vision",
            headers={"Authorization": "Bearer test-token-abc123"},
            json={
                "utterance": "describe what I'm looking at",
                "image_base64": image,
                "retain_frames": True,
                "request_id": "voice-7",
            },
        )
        assert described.status_code == 200
        body = described.json()
        assert body["search"] is False
        assert body["hardware_verified"] is False
        assert "red object" in body["speech"]
        assert body["retained_frame"] is None
        assert body["voice_reply"]["params"]["listen"] is True
        assert not (tmp_path / "api-store" / "frames").exists()

        desktop = client.post(
            "/v1/glasses/vision",
            headers={"Authorization": "Bearer test-token-abc123"},
            json={"utterance": "Jarvis, open Chrome."},
        )
        assert desktop.status_code == 200
        assert desktop.json()["handled"] is False
    config.reset_settings_cache()
    reset_services()


def test_api_rejects_a_bad_device_token(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from cloudos import config
    from cloudos.api.app import app
    from cloudos.glasses.vision.service import reset_services

    monkeypatch.setenv("AGENT_API_TOKEN", "test-token-abc123")
    monkeypatch.setenv("GLASSES_VISION_STORE", str(tmp_path))
    monkeypatch.setenv("GLASSES_VISION_DEVICE_TOKEN", "device-secret")
    config.reset_settings_cache()
    reset_services()
    with TestClient(app, raise_server_exceptions=False) as client:
        rejected = client.post(
            "/v1/glasses/vision",
            headers={"Authorization": "Bearer test-token-abc123"},
            json={"utterance": "describe what I'm looking at", "device_token": "wrong"},
        )
        assert rejected.status_code == 401
    config.reset_settings_cache()
    reset_services()


def test_api_retains_a_frame_only_when_the_machine_opts_in(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from cloudos import config
    from cloudos.api.app import app
    from cloudos.glasses.vision.service import reset_services

    monkeypatch.setenv("AGENT_API_TOKEN", "test-token-abc123")
    monkeypatch.setenv("GLASSES_VISION_STORE", str(tmp_path / "kept"))
    monkeypatch.setenv("GLASSES_VISION_RETAIN_FRAMES", "1")
    config.reset_settings_cache()
    reset_services()
    image = base64.b64encode(to_ppm(_scene(6))).decode("ascii")
    with TestClient(app, raise_server_exceptions=False) as client:
        saved = client.post(
            "/v1/glasses/vision",
            headers={"Authorization": "Bearer test-token-abc123"},
            json={
                "utterance": "describe what I'm looking at",
                "image_base64": image,
                "retain_frames": True,
            },
        )
    assert saved.status_code == 200
    retained = saved.json()["retained_frame"]
    assert retained
    assert Path(retained).is_file()
    assert not any(path.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic"} for path in Path(retained).parent.iterdir())
    config.reset_settings_cache()
    reset_services()


def test_capability_command_prints_the_audit():
    from cloudos.glasses.vision.__main__ import main

    assert main(["capability"]) == 0
