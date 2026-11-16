"""Hand gestures and YOLO categories. No weights download, no paid model, no glasses.

Run: python -m pytest tests/test_glasses_gesture.py -q
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from cloudos.glasses.vision.detector import Detection
from cloudos.glasses.vision.gesture import GestureObjectPipeline
from cloudos.glasses.vision.hands import (
    LANDMARK_COUNT,
    MediaPipeHandLandmarker,
    ScriptedHandLandmarker,
    classify_gesture,
    hand_model_status,
    sample_fist,
    sample_open_palm,
    sample_pinch,
)
from cloudos.glasses.vision.images import solid
from cloudos.glasses.vision.objects import ObjectNameLedger, ObjectTrackBook
from cloudos.glasses.vision.safety import SafetyLimits
from cloudos.glasses.vision.service import VisionService, classify
from cloudos.glasses.vision.yolo import (
    COCO_NAMES,
    MODEL_ID,
    ScriptedObjectDetector,
    YoloObjectDetector,
    YoloUnavailable,
    decode_yolov8,
    yolo_model_status,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _wall() -> str:
    return "2026-11-16T09:46:00+00:00"


def _scores(class_id: int, score: float) -> list[float]:
    values = [0.0] * len(COCO_NAMES)
    values[class_id] = score
    return values


def _row(cx: float, cy: float, width: float, height: float, class_id: int, score: float) -> list[float]:
    return [cx, cy, width, height, *_scores(class_id, score)]


def _cup_and_bottle() -> list[Detection]:
    return [
        Detection("cup", 0.91, (20, 16, 40, 30), model_id="yolo-local"),
        Detection("bottle", 0.8, (70, 10, 20, 20), model_id="yolo-local"),
    ]


def _service(tmp_path: Path, poses: list, clock: Clock) -> VisionService:
    frames = [_cup_and_bottle() for _ in poses]
    return VisionService(
        tmp_path / "store",
        detector=ScriptedObjectDetector(frames),
        hand_landmarker=ScriptedHandLandmarker(poses),
        clock=clock,
        wall_clock=_wall,
        limits=SafetyLimits(max_session_s=20, max_fps=2, max_frames=10),
    )


def test_sample_poses_are_pinch_open_palm_and_fist():
    assert len(sample_open_palm().landmarks) == LANDMARK_COUNT
    assert classify_gesture(sample_open_palm()) == "open_palm"
    assert classify_gesture(sample_fist()) == "fist"
    assert classify_gesture(sample_pinch((0.40, 0.3875))) == "pinch"
    status = hand_model_status(Path("/tmp/missing-hand-landmarker.task"))
    assert status["auto_download"] is False
    assert status["mediapipe_license"] == "Apache-2.0"
    assert status["landmark_count"] == 21
    assert status["hardware_verified"] is False


def test_coarse_confidence_cap_does_not_apply_to_a_yolo_category():
    with pytest.raises(ValueError):
        Detection("red object", 0.97, (0, 0, 4, 4))
    item = Detection("cup", 0.97, (0, 0, 4, 4), model_id="yolo-local")
    assert item.product_model is False
    with pytest.raises(ValueError):
        Detection("cup", 0.5, (0, 0, 4, 4), model_id="yolo-local", product_model=True)


def test_yolo_head_decodes_categories_and_suppresses_overlaps():
    assert len(COCO_NAMES) == 80
    assert COCO_NAMES[41] == "cup"
    image = solid(100, 50, (20, 20, 20))
    rows = [
        _row(50, 50, 10, 10, 41, 0.88),
        _row(52, 50, 10, 10, 41, 0.40),
        _row(10, 30, 8, 8, 39, 0.60),
    ]
    found = YoloObjectDetector(Path("/tmp/not-used.onnx")).detections_from_output(rows, image, input_size=100)
    labels = [(item.label, item.bbox, item.confidence) for item in found]
    assert ("cup", (45, 20, 10, 10), 0.88) in labels
    assert sum(1 for item in found if item.label == "cup") == 1
    assert any(item.label == "bottle" for item in found)
    assert all(item.model_id == MODEL_ID and item.product_model is False for item in found)
    channel_first = [[row[channel] for row in rows] for channel in range(len(rows[0]))]
    again = decode_yolov8(
        channel_first,
        image_width=100,
        image_height=50,
        scale=1.0,
        pad_x=0.0,
        pad_y=25.0,
    )
    assert [item.label for item in again] == [item.label for item in found]


def test_yolo_and_mediapipe_do_not_download(monkeypatch, tmp_path: Path):
    def boom(*_args, **_kwargs):
        raise AssertionError("a vision test tried to use the network")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    status = yolo_model_status(tmp_path / "missing.onnx")
    assert status["auto_download"] is False
    assert status["weights_vendored"] is False
    assert status["ultralytics_package_and_pretrained_weights"] == "AGPL-3.0"
    assert status["onnxruntime_license"] == "MIT"
    detector = YoloObjectDetector(tmp_path / "missing.onnx")
    assert detector.available is False
    with pytest.raises(YoloUnavailable):
        detector.detect(solid(8, 8, (0, 0, 0)))
    landmarker = MediaPipeHandLandmarker(tmp_path / "missing.task")
    assert landmarker.available is False
    with pytest.raises(Exception) as missing:
        landmarker.detect(solid(8, 8, (0, 0, 0)))
    assert "download" in str(missing.value).lower()


def test_tracking_id_is_not_a_durable_identity(tmp_path: Path):
    book = ObjectTrackBook(ledger=ObjectNameLedger(tmp_path / "object-names.json"))
    cup = Detection("cup", 0.9, (10, 10, 20, 20), model_id="yolo-local")
    moved = Detection("cup", 0.9, (12, 10, 20, 20), model_id="yolo-local")
    bottle = Detection("bottle", 0.7, (60, 10, 15, 15), model_id="yolo-local")
    first = book.update([cup, bottle])
    ids = {item.category: item.track_id for item in first}
    assert ids["cup"] == "trk-0001"
    assert ids["bottle"] == "trk-0002"
    assert all(item.durable_id is None for item in first)
    again = book.update([moved, bottle])
    assert {item.track_id for item in again} == {"trk-0001", "trk-0002"}
    named = book.remember("trk-0001", "Blue Mug", consent=True)
    assert named.durable_id == "named-blue-mug"
    assert named.durable_id != named.track_id
    assert named.category == "cup"
    with pytest.raises(Exception):
        book.remember("trk-0002", "Other", consent=False)
    fresh = ObjectTrackBook(ledger=book.ledger)
    revived = fresh.update([cup])
    assert revived[0].track_id == "trk-0001"
    assert revived[0].durable_id is None
    saved = json.loads((tmp_path / "object-names.json").read_text())
    assert saved["auto_match"] is False
    assert saved["raw_images"] == []
    assert saved["names"][0]["name"] == "Blue Mug"
    assert saved["names"][0]["category"] == "cup"


def test_pinch_selects_a_category_then_open_palm_and_fist_reply(tmp_path: Path):
    clock = Clock()
    point = (0.40, 0.3875)
    poses = [sample_pinch(point), sample_open_palm(), sample_pinch(point), sample_fist()]
    service = _service(tmp_path, poses, clock)
    image = solid(100, 80, (12, 12, 12))

    pinched = service.handle("watch my hand", image, request_id="voice-1")
    assert pinched["handled"] is True
    assert pinched["search"] is False
    assert pinched["paid_inference"] is False
    assert pinched["assistant_dispatched"] is False
    assert pinched["hardware_verified"] is False
    assert pinched["route"] == "glasses_vision"
    assert "chatgpt" not in pinched["speech"].lower()
    assert "Pinch" in pinched["speech"]
    assert "trk-0001" in pinched["speech"]
    assert "durable identity" in pinched["speech"]
    gesture = pinched["gesture"]
    assert gesture["gesture"] == "pinch"
    assert gesture["landmark_count"] == 21
    assert len(gesture["landmarks"]) == 21
    assert gesture["evidence"] == "simulated"
    assert gesture["selected"]["category"] == "cup"
    assert gesture["selected"]["track_id"] == "trk-0001"
    assert gesture["selected"]["durable_id"] is None
    assert gesture["selected"]["category"] != "bottle"
    assert gesture["voice_reply"]["method"] == "voice.reply"
    assert gesture["voice_reply"]["params"]["text"] == pinched["speech"]
    assert gesture["voice_reply"]["params"]["listen"] is True
    assert pinched["voice_reply"]["params"]["text"] == pinched["speech"]
    start = gesture["events"][0]
    frame = gesture["events"][-1]
    assert start["kind"] == "start"
    assert start["wall_time"] == "2026-11-16T09:46:00+00:00"
    assert frame["kind"] == "frame"
    assert frame["t_s"] == 0.0
    assert frame["gesture"] == "pinch"
    assert frame["landmark_count"] == 21
    assert "x" not in frame

    clock.now = 0.6
    opened = service.handle("watch my hand", image, request_id="voice-2")
    assert opened["gesture"]["gesture"] == "open_palm"
    assert opened["gesture"]["selected"] is None
    assert "cleared" in opened["speech"]

    clock.now = 1.2
    service.handle("watch my hand", image, request_id="voice-3")
    clock.now = 1.8
    confirmed = service.handle("watch my hand", image, request_id="voice-4")
    assert confirmed["gesture"]["gesture"] == "fist"
    assert "Confirmed cup" in confirmed["speech"]
    assert "No durable identity" in confirmed["speech"]
    assert confirmed["voice_reply"]["method"] == "voice.reply"

    refused = service.handle("remember the selection as Blue Mug", consent=False)
    assert refused["blocked"] == "consent_required"
    assert not (tmp_path / "store" / "object-names.json").exists()

    named = service.handle("remember the selection as Blue Mug", consent=True, request_id="voice-5")
    assert named["gesture"]["selected"]["durable_id"] == "named-blue-mug"
    assert named["gesture"]["selected"]["track_id"] == "trk-0001"
    assert "not inferred again" in named["speech"]
    assert named["assistant_dispatched"] is False

    clock.now = 2.4
    named_fist = service.handle("hand select", image, request_id="voice-6")
    assert "Blue Mug" in named_fist["speech"]
    assert "named-blue-mug" in named_fist["speech"]
    log = named_fist["gesture"]["events"]
    assert any(item["kind"] == "remember" and item["durable_id"] == "named-blue-mug" for item in log)
    assert all(item["wall_time"] == "2026-11-16T09:46:00+00:00" for item in log)


def test_gesture_drops_frames_and_stops_on_heat_cancel_and_battery(tmp_path: Path):
    clock = Clock()
    poses = [sample_pinch((0.40, 0.3875))]
    service = _service(tmp_path, poses, clock)
    image = solid(100, 80, (12, 12, 12))
    service.handle("watch my hand", image)
    clock.now = 0.1
    dropped = service.handle("watch my hand", image)
    assert dropped["gesture"]["dropped"] is True
    assert service.hands.calls == 1

    stopped = service.handle("stop watching my hand")
    assert stopped["stop_reason"] == "user"
    assert stopped["speech"] == "Stopped watching your hand."

    hot = _service(tmp_path / "hot", poses, Clock())
    hot.handle("watch my hand", image)
    overheated = hot.handle("watch my hand", image, thermal_state="hot")
    assert overheated["stop_reason"] == "thermal"
    assert "warm" in overheated["speech"]

    low = _service(tmp_path / "low", poses, Clock())
    refused = low.handle("watch my hand", image, battery_percent=5)
    assert refused["stop_reason"] == "battery"
    assert low.gesture_pipeline.state == "idle"


def test_events_stay_off_disk_unless_retention_is_on(tmp_path: Path):
    clock = Clock()
    service = _service(tmp_path, [sample_open_palm()], clock)
    image = solid(100, 80, (12, 12, 12))
    service.handle("watch my hand", image, retain_frames=True)
    assert not (tmp_path / "store" / "events.jsonl").exists()

    kept = VisionService(
        tmp_path / "kept",
        detector=ScriptedObjectDetector([_cup_and_bottle()]),
        hand_landmarker=ScriptedHandLandmarker([sample_pinch((0.40, 0.3875))]),
        limits=SafetyLimits(retain_frames=True),
        wall_clock=_wall,
    )
    result = kept.handle("select what I'm pinching", image, retain_frames=True, request_id="voice-9")
    path = tmp_path / "kept" / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines
    for line in lines:
        payload = json.loads(line)
        assert "landmarks" not in payload
        assert "x" not in payload
    assert result["gesture"]["landmark_count"] == 21
    assert '"x":' not in path.read_text(encoding="utf-8")


def test_desktop_and_model_routes_do_not_take_the_gesture_phrase(monkeypatch):
    for utterance in (
        "Jarvis, open Chrome.",
        "now click that",
        "Stop Jarvis",
    ):
        assert classify(utterance) is None
    assert classify("watch my hand") == "gesture"
    assert classify("remember the selection as Blue Mug") == "remember_object"

    from cloudos.api import phone

    def fail(*_args, **_kwargs):
        raise AssertionError("gesture phrase reached a model")

    monkeypatch.setattr(phone.claude_cli, "generate", fail)
    monkeypatch.setattr(phone.codex_cli, "generate", fail)
    result = phone.execute_phone_task("watch my hand", "auto")
    assert result["agent"] == "glasses-vision"
    assert result["search"] is False


def test_api_gesture_does_not_invent_landmarks(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from cloudos import config
    from cloudos.api.app import app
    from cloudos.glasses.vision.images import to_ppm
    from cloudos.glasses.vision.service import reset_services

    monkeypatch.setenv("AGENT_API_TOKEN", "test-token-abc123")
    monkeypatch.setenv("GLASSES_VISION_STORE", str(tmp_path / "api"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config.reset_settings_cache()
    reset_services()
    image = base64.b64encode(to_ppm(solid(32, 24, (20, 20, 20)))).decode("ascii")
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/glasses/vision",
            headers={"Authorization": "Bearer test-token-abc123"},
            json={
                "utterance": "watch my hand",
                "image_base64": image,
                "request_id": "voice-api",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["search"] is False
    assert body["paid_inference"] is False
    assert body["assistant_dispatched"] is False
    assert body["hardware_verified"] is False
    assert body["gesture"]["gesture"] == "unavailable"
    assert body["gesture"]["landmark_count"] == 0
    assert body["voice_reply"]["method"] == "voice.reply"
    assert "MediaPipe" in body["speech"]
    assert "invent" in body["speech"]
    config.reset_settings_cache()
    reset_services()


def test_direct_pipeline_matches_the_same_selection_rules(tmp_path: Path):
    clock = Clock()
    pipeline = GestureObjectPipeline(
        tmp_path,
        landmarker=ScriptedHandLandmarker([sample_pinch((0.40, 0.3875))]),
        detector=ScriptedObjectDetector([_cup_and_bottle()]),
        limits=SafetyLimits(),
        clock=clock,
        wall_clock=_wall,
    )
    result = pipeline.step(solid(100, 80, (1, 1, 1)), request_id="direct")
    assert result["paid_inference"] is False
    assert result["assistant_dispatched"] is False
    assert result["evidence"] == "simulated"
    assert result["selected"]["track_id"] == "trk-0001"
    assert result["events"][-1]["t_s"] == 0.0
