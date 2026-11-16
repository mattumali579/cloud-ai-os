"""Crowded-scene selection. Synthetic frames only. No model API and no glasses.

Run: python -m pytest tests/test_glasses_selection.py -q
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from cloudos.glasses.vision.detector import Detection
from cloudos.glasses.vision.hands import ScriptedHandLandmarker, sample_open_palm, sample_pinch
from cloudos.glasses.vision.images import paint_rect, parse_ppm, solid
from cloudos.glasses.vision.safety import SafetyLimits
from cloudos.glasses.vision.service import VisionService, classify
from cloudos.glasses.vision.yolo import ScriptedObjectDetector


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _wall() -> str:
    return "2026-11-16T10:05:00+00:00"


def _trucks() -> list[Detection]:
    return [
        Detection("truck", 0.9, (4, 20, 24, 24), model_id="yolo-local"),
        Detection("truck", 0.86, (70, 20, 24, 24), model_id="yolo-local"),
    ]


def _scene():
    image = solid(120, 80, (30, 30, 30))
    image = paint_rect(image, 4, 20, 24, 24, (220, 20, 20))
    return paint_rect(image, 70, 20, 24, 24, (20, 40, 220))


def _service(tmp_path: Path, batches: list[list[Detection]], clock: Clock, poses=None) -> VisionService:
    return VisionService(
        tmp_path / "store",
        detector=ScriptedObjectDetector(batches),
        hand_landmarker=ScriptedHandLandmarker(poses or []),
        limits=SafetyLimits(max_session_s=20, max_fps=2, max_frames=10, lost_after_misses=1),
        clock=clock,
        wall_clock=_wall,
    )


def test_voice_commands_are_selection_and_desktop_commands_are_not():
    assert classify("scan objects") == "select"
    assert classify("track the truck on the left") == "select"
    assert classify("select object 3") == "select"
    assert classify("switch to the other truck") == "select"
    assert classify("what am I tracking?") == "select"
    assert classify("log this object") == "select"
    assert classify("point at an object") == "select"
    assert classify("control the truck") == "select"
    assert classify("stop tracking") == "stop_tracking"
    assert classify("track that object") == "track"
    assert classify("Jarvis, open Chrome.") is None
    assert classify("now click that") is None


def test_scan_numbers_preview_and_does_not_auto_select_two_trucks(tmp_path: Path):
    clock = Clock()
    service = _service(tmp_path, [_trucks()], clock)
    result = service.handle("scan objects", _scene(), request_id="voice-scan")
    assert result["paid_inference"] is False
    assert result["live_ai_used"] is False
    assert result["live_view_modified"] is False
    assert result["assistant_dispatched"] is False
    assert result["selection"]["locked_track_id"] is None
    assert "did not" not in result["speech"] or "not in the lenses" in result["speech"]
    assert "not in the lenses" in result["speech"]
    preview = result["preview"]
    assert preview["surface"] == "phone_or_pc"
    assert preview["lens_hud"] is False
    classes = {item["class"] for item in preview["candidates"]}
    assert classes == {"truck"}
    locations = {item["location"] for item in preview["candidates"]}
    assert locations == {"left", "right"}
    appearances = {item["appearance"] for item in preview["candidates"]}
    assert "red" in appearances
    assert "blue" in appearances
    assert all(item["durable_identity"] is False for item in preview["candidates"])
    drawn = parse_ppm(base64.b64decode(preview["ppm_base64"]))
    assert drawn.pixel(4, 20) == (255, 220, 0)
    assert result["voice_reply"]["method"] == "voice.reply"
    assert result["voice_reply"]["params"]["text"] == result["speech"]


def test_side_query_locks_one_truck_and_refuses_two_on_the_same_side(tmp_path: Path):
    clock = Clock()
    crowded = [
        Detection("truck", 0.9, (2, 10, 16, 16), model_id="yolo-local"),
        Detection("truck", 0.88, (22, 10, 16, 16), model_id="yolo-local"),
    ]
    service = _service(tmp_path, [crowded, crowded], clock)
    image = solid(120, 60, (20, 20, 20))
    ambiguous = service.handle("track the truck on the left", image, request_id="voice-ask")
    assert ambiguous["selection"]["locked_track_id"] is None
    assert ambiguous["blocked"] == "ambiguous"
    assert "I did not pick one" in ambiguous["speech"]
    assert "object 1" in ambiguous["speech"]
    assert "object 2" in ambiguous["speech"]

    clear = _service(tmp_path / "one", [_trucks(), _trucks()], clock)
    chosen = clear.handle("track the truck on the left", _scene(), request_id="voice-lock")
    assert chosen["selection"]["locked_track_id"] == "trk-0001"
    assert chosen["selection"]["selection_method"] == "voice"
    assert chosen["selection"]["candidate"]["location"] == "left"
    assert "only that id" in chosen["speech"]


def test_select_number_switch_status_and_stop(tmp_path: Path):
    clock = Clock()
    batches = [_trucks() for _ in range(6)]
    service = _service(tmp_path, batches, clock)
    image = _scene()
    service.handle("scan objects", image)
    clock.now = 0.6
    picked = service.handle("select object 2", image, request_id="voice-2")
    assert picked["selection"]["locked_track_id"] == "trk-0002"
    assert picked["selection"]["candidate"]["location"] == "right"

    clock.now = 1.2
    switched = service.handle("switch to the other truck", image)
    assert switched["selection"]["locked_track_id"] == "trk-0001"
    assert "only that id" in switched["speech"]

    clock.now = 1.8
    status = service.handle("what am I tracking?")
    assert "trk-0001" in status["speech"]
    assert "session" in status["speech"]

    stopped = service.handle("stop tracking")
    assert stopped["speech"] == "Stopped tracking."
    assert stopped["stop_reason"] == "user"
    assert service.selection.locked_track_id is None


def test_follow_does_not_hop_to_the_other_truck(tmp_path: Path):
    clock = Clock()
    only_right = [Detection("truck", 0.9, (70, 20, 24, 24), model_id="yolo-local")]
    service = _service(tmp_path, [_trucks(), only_right], clock)
    image = _scene()
    service.handle("track the truck on the left", image)
    clock.now = 0.6
    lost = service.handle("what am I tracking?", image)
    assert lost["stop_reason"] == "lost"
    assert service.selection.locked_track_id is None
    assert "did not switch" in lost["speech"]
    assert lost["selection"]["locked_track_id"] is None


def test_pinch_needs_a_second_frame_and_points_at_one_box(tmp_path: Path):
    clock = Clock()
    # Pinch tip sits in the left truck. sample_pinch puts landmarks 4 and 8 there.
    tip = ((4 + 12) / 120, (20 + 12) / 80)
    poses = [sample_pinch(tip), sample_pinch(tip), sample_open_palm(), sample_pinch(tip)]
    service = _service(tmp_path, [_trucks() for _ in range(4)], clock, poses)
    image = _scene()
    first = service.handle("point at an object", image, request_id="voice-point")
    assert first["selection"]["locked_track_id"] is None
    assert first["blocked"] == "debounce"
    assert "not locked" in first["speech"]
    assert first["selection"]["gaze"] is False
    assert first["selection"]["physical_actuation"] is False
    assert first["selection"]["intrinsics_calibrated"] is False

    clock.now = 0.6
    second = service.handle("point at an object", image, request_id="voice-point-2")
    assert second["selection"]["locked_track_id"] == "trk-0001"
    assert second["selection"]["selection_method"] == "pinch"
    assert "Pinch" in second["speech"] or "pinch" in second["speech"]

    fresh = _service(
        tmp_path / "reset",
        [_trucks(), _trucks(), _trucks()],
        Clock(),
        [sample_pinch(tip), sample_open_palm(), sample_pinch(tip)],
    )
    fresh.handle("point at an object", image)
    fresh.selection  # streak is 1
    clock2 = fresh._clock
    clock2.now = 0.6
    opened = fresh.handle("point at an object", image)
    assert opened["selection"]["locked_track_id"] is None
    clock2.now = 1.2
    again = fresh.handle("point at an object", image)
    assert again["blocked"] == "debounce"
    assert again["selection"]["locked_track_id"] is None


def test_log_writes_trajectory_without_a_model_call(tmp_path: Path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("selection test tried to use the network")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    clock = Clock()
    service = _service(tmp_path, [_trucks(), _trucks()], clock)
    image = _scene()
    service.handle("track the truck on the left", image)
    clock.now = 0.6
    service.handle("what am I tracking?", image)
    logged = service.handle("log this object")
    assert "Logged trk-0001" in logged["speech"]
    lines = (tmp_path / "store" / "selection-log.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in lines]
    assert "lock" in kinds
    assert "log" in kinds
    record = json.loads(lines[-1])
    assert record["track_id"] == "trk-0001"
    assert record["session_scoped"] is True
    assert record["physical_actuation"] is False
    assert len(record["trajectory"]) >= 2
    assert record["wall_time"] == "2026-11-16T10:05:00+00:00"
    assert "landmarks" not in record


def test_control_object_is_software_only(tmp_path: Path):
    service = _service(tmp_path, [_trucks()], Clock())
    result = service.handle("control the truck", _scene(), request_id="voice-control")
    assert result["blocked"] == "software_only"
    assert result["selection"]["locked_track_id"] is None
    assert result["selection"]["physical_actuation"] is False
    assert "hardware-control" in result["speech"]
    assert result["live_ai_used"] is False


def test_phone_task_holds_scan_off_the_model(monkeypatch):
    from cloudos.api import phone

    def fail(*_args, **_kwargs):
        raise AssertionError("scan phrase reached a model")

    monkeypatch.setattr(phone.claude_cli, "generate", fail)
    monkeypatch.setattr(phone.codex_cli, "generate", fail)
    result = phone.execute_phone_task("scan objects", "auto")
    assert result["agent"] == "glasses-vision"
    assert result["search"] is False
