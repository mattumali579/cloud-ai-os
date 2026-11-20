#!/usr/bin/env python3
"""Decode real 10917.mp4 with production models and render a replay (issue #39)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from tracker_port import Box, INDEX_MCP, INDEX_TIP, WRIST

# Honest COCO names only. Do not map cup->barrel or book->phone.
SEMANTIC = {
    "phone": {"cell phone"},
    "chair": {"chair"},
}

MODULE = Path(__file__).resolve().parents[2]
WORKTREE = Path(__file__).resolve().parents[4]
DEFAULT_VIDEO = WORKTREE / "test-recordings" / "10917" / "10917.mp4"
DOWNLOADS = Path.home() / "Downloads" / "10917.mp4"
MODELS = MODULE / "app" / "build" / "generated" / "visionAssets"
GT_PATH = Path(__file__).with_name("ground_truth_10917.json")
OBJECT_MAX_W = 384
HAND_MAX_W = 768
HAND_SCORE = 0.15
OBJECT_SCORE = 0.25


def output_size(w: int, h: int, max_w: int) -> tuple[int, int]:
    width = min(w, max_w)
    height = max(1, int(width / w * h))
    return width, height


def mask_hud(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    out = frame.copy()
    regions = [
        (0, 0, int(w * 0.42), int(h * 0.14)),
        (int(w * 0.52), 0, w, int(h * 0.22)),
        (0, int(h * 0.92), w, h),
    ]
    for x0, y0, x1, y1 in regions:
        sample_y = min(h - 1, y1 + 2 if y1 < h - 2 else max(0, y0 - 2))
        fill = out[sample_y, x0:x1].mean(axis=0) if x1 > x0 else out[sample_y, 0]
        out[y0:y1, x0:x1] = fill
    return out


def scale_boxes(boxes: list[Box], sx: float, sy: float) -> list[Box]:
    scaled = []
    for b in boxes:
        scaled.append(
            Box(
                kind=b.kind,
                label=b.label,
                score=b.score,
                left=b.left * sx,
                top=b.top * sy,
                right=b.right * sx,
                bottom=b.bottom * sy,
                points=[(p[0] * sx, p[1] * sy) for p in b.points],
                depths=list(b.depths),
            )
        )
    return scaled


def load_detectors():
    from mediapipe.tasks.python.core.base_options import BaseOptions
    from mediapipe.tasks.python.vision import (
        HandLandmarker,
        HandLandmarkerOptions,
        ObjectDetector,
        ObjectDetectorOptions,
        RunningMode,
    )

    obj_model = MODELS / "efficientdet_lite0.tflite"
    hand_model = MODELS / "hand_landmarker.task"
    if not obj_model.exists() or not hand_model.exists():
        raise SystemExit(f"Missing production models in {MODELS}")
    objects = ObjectDetector.create_from_options(
        ObjectDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(obj_model)),
            running_mode=RunningMode.IMAGE,
            score_threshold=OBJECT_SCORE,
            max_results=8,
        )
    )
    hands = HandLandmarker.create_from_options(
        HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(hand_model)),
            running_mode=RunningMode.VIDEO,
            num_hands=2,
            min_hand_detection_confidence=HAND_SCORE,
            min_hand_presence_confidence=HAND_SCORE,
            min_tracking_confidence=HAND_SCORE,
        )
    )
    return objects, hands


def detect_objects(detector, bgr: np.ndarray) -> list[Box]:
    import mediapipe as mp

    h, w = bgr.shape[:2]
    ow, oh = output_size(w, h, OBJECT_MAX_W)
    small = cv2.resize(bgr, (ow, oh), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = detector.detect(mp_image)
    boxes = []
    for det in result.detections:
        cat = det.categories[0]
        bb = det.bounding_box
        name = cat.category_name or "object"
        score = float(cat.score or 0)
        boxes.append(
            Box(
                kind="object",
                label=name,
                score=score,
                left=float(bb.origin_x),
                top=float(bb.origin_y),
                right=float(bb.origin_x + bb.width),
                bottom=float(bb.origin_y + bb.height),
            )
        )
    return scale_boxes(boxes, w / ow, h / oh)


def detect_hands(landmarker, bgr: np.ndarray, ts_ms: int) -> list[Box]:
    import mediapipe as mp

    h, w = bgr.shape[:2]
    ow, oh = output_size(w, h, HAND_MAX_W)
    small = cv2.resize(bgr, (ow, oh), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = landmarker.detect_for_video(mp_image, ts_ms)
    boxes = []
    handed = result.handedness if hasattr(result, "handedness") and result.handedness else []
    for idx, lm in enumerate(result.hand_landmarks):
        if len(lm) < 21:
            continue
        score = 0.0
        if idx < len(handed) and len(handed[idx]) > 0:
            score = float(handed[idx][0].score)
        if score < HAND_SCORE:
            continue

        def landmark_reliable(p) -> bool:
            presence = getattr(p, "presence", None)
            if presence is None:
                presence = 1.0
            visibility = getattr(p, "visibility", None)
            if visibility is None:
                visibility = 1.0
            return presence >= HAND_SCORE or visibility >= HAND_SCORE

        if not (landmark_reliable(lm[WRIST]) or landmark_reliable(lm[INDEX_MCP]) or landmark_reliable(lm[INDEX_TIP])):
            continue

        pts = [(p.x * w, p.y * h) for p in lm]
        depths = [float(p.z * w) for p in lm]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        boxes.append(
            Box(
                kind="hand",
                label="hand",
                score=score,
                left=min(xs),
                top=min(ys),
                right=max(xs),
                bottom=max(ys),
                points=pts,
                depths=depths,
            )
        )
    return boxes


def segment_at(gt: dict, t: float) -> dict:
    for seg in gt["segments"]:
        if seg["t0"] <= t < seg["t1"]:
            return seg
    return gt["segments"][-1]


def lock_in_region(lock_box: Box | None, region: list[float] | None, fw: int, fh: int) -> bool:
    if lock_box is None or not region:
        return False
    cx = (lock_box.left + lock_box.right) * 0.5 / fw
    cy = (lock_box.top + lock_box.bottom) * 0.5 / fh
    x0, y0, x1, y1 = region
    return x0 <= cx <= x1 and y0 <= cy <= y1


def draw(frame: np.ndarray, boxes: list[Box], lock_id: int | None, aim, t: float, hud: str) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]
    for box in boxes:
        color = (80, 80, 80)
        if box.kind == "hand":
            color = (0, 220, 255)
        elif box.track_id == lock_id:
            color = (0, 255, 0)
        elif box.label.lower() == "person":
            color = (0, 0, 255)
        else:
            color = (255, 180, 0)
        cv2.rectangle(out, (int(box.left), int(box.top)), (int(box.right), int(box.bottom)), color, 2)
        cv2.putText(
            out,
            f"{box.label}#{box.track_id}",
            (int(box.left), max(18, int(box.top) - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
        if box.kind == "hand" and len(box.points) >= 21:
            for p in box.points:
                cv2.circle(out, (int(p[0]), int(p[1])), 3, (0, 255, 255), -1)
            tip = box.points[8]
            cv2.circle(out, (int(tip[0]), int(tip[1])), 8, (0, 0, 255), 2)
    if aim and aim.ray:
        r = aim.ray
        end = (int(r.tip_x + r.dir_x * r.reach * 0.35), int(r.tip_y + r.dir_y * r.reach * 0.35))
        cv2.line(out, (int(r.tip_x), int(r.tip_y)), end, (0, 255, 0), 3)
    cv2.rectangle(out, (0, 0), (w, 70), (0, 0, 0), -1)
    cv2.putText(out, hud, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 180), 1, cv2.LINE_AA)
    cv2.putText(out, f"t={t:.2f}s replay (masked HUD)", (12, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
    return out


def ensure_video(path: Path) -> Path:
    if path.exists():
        return path
    if DOWNLOADS.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DOWNLOADS, path)
        return path
    raise SystemExit(f"10917.mp4 not found at {path} or {DOWNLOADS}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--out", type=Path, default=WORKTREE / "test-recordings" / "10917" / "replay")
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--skip-detect", action="store_true")
    args = parser.parse_args()
    video_path = ensure_video(args.video)
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    gt = json.loads(GT_PATH.read_text(encoding="utf-8"))
    cap = cv2.VideoCapture(str(video_path))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(src_fps / args.fps)))
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if args.skip_detect:
        detections = load_saved_detections(out_dir / "detections.txt")
        cap.release()
    else:
        objects_det, hands_det = load_detectors()
        detections = []
    if not args.skip_detect:
        frame_i = 0
        ts_ms = 0
    while (not args.skip_detect) and True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_i % step != 0:
            frame_i += 1
            continue
        t = frame_i / src_fps
        masked = mask_hud(frame)
        objs = detect_objects(objects_det, masked)
        hands = detect_hands(hands_det, masked, ts_ms)
        ts_ms += int(1000 / args.fps)
        detections.append(
            {
                "t": round(t, 3),
                "width": fw,
                "height": fh,
                "boxes": [box_payload(b) for b in objs + hands],
            }
        )
        frame_i += 1
        if len(detections) % 25 == 0:
            print(f"detected {len(detections)} frames", flush=True)
    if not args.skip_detect:
        cap.release()
        (out_dir / "detections.txt").write_text(
            "".join(detection_record(row) for row in detections),
            encoding="utf-8",
        )
    run_kotlin(out_dir)
    decisions = [
        json.loads(line)
        for line in (out_dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(decisions) != len(detections):
        raise SystemExit(f"Kotlin returned {len(decisions)} decisions for {len(detections)} frames")
    writer = cv2.VideoWriter(
        str(out_dir / "10917_replay.mp4"),
        cv2.VideoWriter_fourcc(*"mp4v"),
        args.fps,
        (fw, fh),
    )
    cap = cv2.VideoCapture(str(video_path))
    rows = []
    frame_i = 0
    sample = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_i % step != 0:
            frame_i += 1
            continue
        decision = decisions[sample]
        detected = detections[sample]
        sample += 1
        t = detected["t"]
        boxes = [box_from_decision(raw) for raw in decision["boxes"]]
        locked = decision["lock_id"]
        lock_box = next((b for b in boxes if b.track_id == locked), None)
        seg = segment_at(gt, t)
        source = {
            "POINTING_CONFIRMED": "mediapipe",
            "HAND_VISIBLE": "mediapipe",
            "ARM_VISIBLE": "arm_box",
        }.get(decision["state"], "miss")
        person_shown = any(b.label.lower() == "person" for b in boxes)
        hand_ok = (seg["hand"] != "pointing") or any(b.kind == "hand" for b in boxes)
        target_ok = True
        steal = False
        if seg["target"] == "none":
            target_ok = lock_box is None or not lock_in_region(lock_box, [0.48, 0.62, 0.92, 0.96], fw, fh)
        else:
            target_ok = lock_in_region(lock_box, seg.get("region"), fw, fh)
            steal = lock_in_region(lock_box, [0.48, 0.62, 0.92, 0.96], fw, fh) and seg["target"] != "phone"
        ray_hit = False
        if seg["target"] != "none" and decision["ray"] and seg.get("region"):
            region = seg["region"]
            ray_hit = ray_enters(
                decision["tip_x"],
                decision["tip_y"],
                decision["dir_x"],
                decision["dir_y"],
                decision["reach"],
                region[0] * fw,
                region[1] * fh,
                region[2] * fw,
                region[3] * fh,
            )
        proposal = False
        if seg.get("region"):
            proposal = any(center_in_region(b, seg["region"], fw, fh) for b in boxes)
        label = lock_box.label if lock_box else None
        expected = SEMANTIC.get(seg["target"], set())
        if seg["target"] in ("none",) or label is None or label == "object":
            semantic = "unscored"
        elif label in expected:
            semantic = "correct"
        else:
            semantic = "wrong"
        hud = (
            f"{decision['state']} hands={sum(1 for b in boxes if b.kind == 'hand')} "
            f"lock={lock_box.label if lock_box else '-'} gt={seg['target']}"
        )
        aim = None
        if decision["ray"]:
            aim = DrawAim(
                DrawRay(
                    decision["tip_x"],
                    decision["tip_y"],
                    decision["dir_x"],
                    decision["dir_y"],
                    decision["reach"],
                )
            )
        writer.write(draw(frame, boxes, locked, aim, t, hud))
        rows.append(
            {
                "t": t,
                "gesture": decision["state"],
                "hand_source": source,
                "hands": sum(1 for b in boxes if b.kind == "hand"),
                "person_fp": person_shown,
                "lock_label": label,
                "lock_id": locked,
                "ray": bool(decision["ray"]),
                "hit_id": decision["hit_id"],
                "gt_hand": seg["hand"],
                "gt_target": seg["target"],
                "holdout": bool(seg.get("holdout")),
                "hand_ok": hand_ok,
                "target_ok": target_ok,
                "ray_hits_gt": ray_hit,
                "proposal_in_gt": proposal,
                "semantic": semantic,
                "phone_steal": steal,
                "phone_miss": seg["target"] == "phone" and not target_ok,
            }
        )
        frame_i += 1
    cap.release()
    writer.release()
    n = max(1, len(rows))
    pointing = [r for r in rows if r["gt_hand"] == "pointing"]
    aimed_rows = [r for r in pointing if r["gt_target"] != "none"]
    phone_rows = [r for r in pointing if r["gt_target"] == "phone"]
    other_rows = [r for r in pointing if r["gt_target"] not in ("phone", "none")]
    tune = [r for r in aimed_rows if not r["holdout"]]
    hold = [r for r in aimed_rows if r["holdout"]]
    scored = [r for r in aimed_rows if r["semantic"] != "unscored"]

    def rate(pred, group):
        return round(sum(1 for r in group if pred(r)) / max(1, len(group)), 4)

    metrics = {
        "frames": n,
        "pointing_frames": len(pointing),
        "video": str(video_path),
        "replay_mp4": str(out_dir / "10917_replay.mp4"),
        "hand_recall_while_pointing": rate(lambda r: r["hands"] > 0, pointing),
        "mediapipe_rate_while_pointing": rate(lambda r: r["hand_source"] == "mediapipe", pointing),
        "arm_box_rate_while_pointing": rate(lambda r: r["hand_source"] == "arm_box", pointing),
        "held_rate_while_pointing": rate(lambda r: r["hand_source"] == "held", pointing),
        "miss_rate_while_pointing": rate(lambda r: r["hand_source"] == "miss", pointing),
        "person_fp_rate": round(sum(r["person_fp"] for r in rows) / n, 4),
        "geometric_lock_hit_rate": rate(lambda r: r["target_ok"], aimed_rows),
        "geometric_ray_hit_rate": rate(lambda r: r["ray_hits_gt"], aimed_rows),
        "geometric_ray_hit_rate_tune": rate(lambda r: r["ray_hits_gt"], tune),
        "geometric_ray_hit_rate_holdout": rate(lambda r: r["ray_hits_gt"], hold),
        "geometric_lock_hit_rate_mediapipe": rate(
            lambda r: r["target_ok"], [r for r in aimed_rows if r["hand_source"] == "mediapipe"]
        ),
        "target_hit_rate": rate(lambda r: r["target_ok"], pointing),
        "semantic_correct": sum(r["semantic"] == "correct" for r in scored),
        "semantic_wrong": sum(r["semantic"] == "wrong" for r in scored),
        "semantic_scored_frames": len(scored),
        "phone_steal_count": sum(r["phone_steal"] for r in pointing),
        "phone_steal_rate": rate(lambda r: r["phone_steal"], pointing),
        "phone_steal_rate_other_targets": rate(lambda r: r["phone_steal"], other_rows),
        "phone_miss_rate": rate(lambda r: r["phone_miss"], phone_rows),
        "phone_frames": len(phone_rows),
        "ray_rate_while_pointing": rate(lambda r: r["ray"], pointing),
        "arm_box_frames": sum(r["hand_source"] == "arm_box" for r in rows),
        "mediapipe_frames": sum(r["hand_source"] == "mediapipe" for r in rows),
        "held_frames": sum(r["hand_source"] == "held" for r in rows),
        "miss_frames": sum(r["hand_source"] == "miss" for r in rows),
        "tune_frames": len(tune),
        "holdout_frames": len(hold),
        "holdout_segments": ["chair", "optics"],
        "source": "composited_screen_recording",
        "model": "efficientdet_lite0",
        "note": "Locks come from ProductionTracker. An arm box is not a finger. semantic does not rename book to phone or cup to barrel. Zero locks fails. This is a screen recording, not glasses proof.",
    }
    locked_rows = [r for r in rows if r["lock_id"] is not None]
    correct_locks = [r for r in aimed_rows if r["target_ok"] and r["lock_id"] is not None]
    none_rows = [r for r in rows if r["gt_target"] == "none"]
    delays = []
    for seg in gt["segments"]:
        if seg["target"] == "none" or seg.get("holdout"):
            continue
        hits = [r["t"] for r in rows if seg["t0"] <= r["t"] < seg["t1"] and r["target_ok"] and r["lock_id"] is not None]
        if hits:
            delays.append(hits[0] - seg["t0"])
    metrics["lock_frames"] = len(locked_rows)
    metrics["lock_recall"] = rate(lambda r: r["target_ok"] and r["lock_id"] is not None, aimed_rows)
    metrics["lock_precision"] = round(len(correct_locks) / max(1, len([r for r in aimed_rows if r["lock_id"] is not None])), 4)
    metrics["no_target_false_lock_rate"] = rate(lambda r: r["lock_id"] is not None, none_rows)
    metrics["median_acquire_delay_s"] = round(sorted(delays)[len(delays) // 2], 3) if delays else None
    per_target = {}
    for name in sorted({r["gt_target"] for r in aimed_rows}):
        group = [r for r in aimed_rows if r["gt_target"] == name]
        per_target[name] = {
            "frames": len(group),
            "holdout": bool(group and group[0]["holdout"]),
            "lock_recall": rate(lambda r: r["target_ok"] and r["lock_id"] is not None, group),
            "lock_frames": sum(1 for r in group if r["target_ok"] and r["lock_id"] is not None),
            "ray_hit": rate(lambda r: r["ray_hits_gt"], group),
            "proposal_coverage": rate(lambda r: r["proposal_in_gt"], group),
        }
    metrics["per_target"] = per_target
    metrics["decision_source"] = "ProductionTracker"
    zero_lock_targets = sum(1 for item in per_target.values() if item["lock_recall"] <= 0)
    (out_dir / "frames.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    # Zero locks is a failure. Six of seven targets at 0% lock recall is a failure.
    # Phone steal of zero does not pass either of those.
    fail = (
        metrics["lock_frames"] == 0
        or metrics["lock_recall"] <= 0
        or metrics["phone_steal_rate_other_targets"] > 0.08
        or zero_lock_targets >= 6
    )
    return 1 if fail else 0


def load_saved_detections(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"missing {path}; run without --skip-detect")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("FRAME "):
            continue
        _, width, height, t = line.split()
        rows.append({"t": float(t), "width": int(width), "height": int(height), "boxes": []})
    if not rows:
        raise SystemExit(f"{path} has no FRAME lines")
    return rows


def detection_record(row: dict) -> str:
    lines = [f"FRAME {row['width']} {row['height']} {row['t']}"]
    for box in row["boxes"]:
        label = json.dumps(box["label"], ensure_ascii=True).replace(" ", "\\u0020")
        lines.append(
            "BOX {kind} {label} {score} {left} {top} {right} {bottom}".format(
                kind=box["kind"],
                label=label,
                score=box["score"],
                left=box["left"],
                top=box["top"],
                right=box["right"],
                bottom=box["bottom"],
            )
        )
        if box.get("points"):
            flat = " ".join(f"{p[0]} {p[1]}" for p in box["points"])
            lines.append("POINTS " + flat)
        if box.get("depths"):
            flat_d = " ".join(f"{d}" for d in box["depths"])
            lines.append("DEPTHS " + flat_d)
    lines.append("END")
    return "\n".join(lines) + "\n"


def box_payload(box: Box) -> dict:
    payload = {
        "kind": box.kind,
        "label": box.label,
        "score": box.score,
        "left": box.left,
        "top": box.top,
        "right": box.right,
        "bottom": box.bottom,
    }
    if box.points:
        payload["points"] = [[p[0], p[1]] for p in box.points]
    if box.depths:
        payload["depths"] = [float(d) for d in box.depths]
    return payload


def box_from_decision(raw: dict) -> Box:
    return Box(
        kind=raw["kind"],
        label=raw["label"],
        score=float(raw["score"]),
        left=float(raw["left"]),
        top=float(raw["top"]),
        right=float(raw["right"]),
        bottom=float(raw["bottom"]),
        points=[(p[0], p[1]) for p in raw.get("points", [])],
        track_id=int(raw.get("track_id") or 0),
    )


class DrawRay:
    def __init__(self, tip_x: float, tip_y: float, dir_x: float, dir_y: float, reach: float) -> None:
        self.tip_x = tip_x
        self.tip_y = tip_y
        self.dir_x = dir_x
        self.dir_y = dir_y
        self.reach = reach


class DrawAim:
    def __init__(self, ray: DrawRay) -> None:
        self.ray = ray


def center_in_region(box: Box, region: list[float], fw: int, fh: int) -> bool:
    if box.kind != "object" or box.label.lower() == "person":
        return False
    cx = (box.left + box.right) * 0.5 / fw
    cy = (box.top + box.bottom) * 0.5 / fh
    x0, y0, x1, y1 = region
    return x0 <= cx <= x1 and y0 <= cy <= y1


def ray_enters(tip_x, tip_y, dir_x, dir_y, reach, x0, y0, x1, y1) -> bool:
    if x0 <= tip_x <= x1 and y0 <= tip_y <= y1:
        return True
    t_min = 0.0
    t_max = float(reach)
    origin = (tip_x, tip_y)
    direction = (dir_x, dir_y)
    bounds = ((x0, x1), (y0, y1))
    for axis in (0, 1):
        if abs(direction[axis]) < 1e-4:
            if origin[axis] < bounds[axis][0] or origin[axis] > bounds[axis][1]:
                return False
            continue
        near = (bounds[axis][0] - origin[axis]) / direction[axis]
        far = (bounds[axis][1] - origin[axis]) / direction[axis]
        if near > far:
            near, far = far, near
        t_min = max(t_min, near)
        t_max = min(t_max, far)
        if t_min > t_max:
            return False
    return True


def run_kotlin(out_dir: Path) -> None:
    temp = Path(os.environ.get("TEMP", ""))
    candidates = [
        temp / "gradle-extract" / "gradle-9.6.0" / "bin" / "gradle.bat",
        temp / "gradle-9.6.0" / "bin" / "gradle.bat",
    ]
    gradle = next((path for path in candidates if path.exists()), None)
    if gradle is None:
        raise SystemExit("gradle.bat not found; ProductionTracker replay cannot run")
    env = os.environ.copy()
    if "ANDROID_HOME" not in env:
        env["ANDROID_HOME"] = r"C:\Users\Admin\AppData\Local\Android\Sdk"
    env["VISOR_REPLAY_DIR"] = str(out_dir)
    print("ProductionTracker decisions", flush=True)
    subprocess.run(
        [
            str(gradle),
            ":app:testDebugUnitTest",
            "--offline",
            "--rerun-tasks",
            "--tests",
            "com.mattumali.visortracker.ReplayDriverTest",
        ],
        cwd=MODULE,
        env=env,
        check=True,
    )


if __name__ == "__main__":
    sys.exit(main())
