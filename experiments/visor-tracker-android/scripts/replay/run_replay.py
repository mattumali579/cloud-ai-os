#!/usr/bin/env python3
"""Decode real 10917.mp4 with production models and render a replay (issue #39)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

from tracker_port import (
    AimLock,
    Box,
    TrackIds,
    aimed,
    entered,
    from_arm_box,
    hand_priority,
    wearer_arm,
)

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
        if name.lower() != "person" and score < 0.45:
            name = "object"
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
    sx, sy = w / ow, h / oh
    boxes = []
    for lm in result.hand_landmarks:
        pts = [(p.x * ow * sx, p.y * oh * sy) for p in lm]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        boxes.append(
            Box(
                kind="hand",
                label="hand",
                score=0.9,
                left=min(xs),
                top=min(ys),
                right=max(xs),
                bottom=max(ys),
                points=pts,
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
    args = parser.parse_args()
    video_path = ensure_video(args.video)
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    gt = json.loads(GT_PATH.read_text(encoding="utf-8"))
    objects_det, hands_det = load_detectors()
    cap = cv2.VideoCapture(str(video_path))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(src_fps / args.fps)))
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(
        str(out_dir / "10917_replay.mp4"),
        cv2.VideoWriter_fourcc(*"mp4v"),
        args.fps,
        (fw, fh),
    )
    tracks = TrackIds()
    lock = AimLock()
    recent_hands: list[Box] = []
    hand_misses = 0
    rows = []
    frame_i = 0
    ts_ms = 0
    while True:
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
        arm = next((b for b in objs if wearer_arm(b, fw, fh)), None)
        planar = [h for h in hands if has_planar(h)]
        if planar:
            recent_hands = planar
            hand_misses = 0
            source = "mediapipe"
        elif arm is not None:
            recent_hands = [from_arm_box(arm, fw, fh)]
            hand_misses = 0
            source = "arm_box"
        elif hands:
            recent_hands = hands
            hand_misses = 0
            source = "mediapipe"
        else:
            hand_misses += 1
            keep = any(extended_ok(h, fw, fh) for h in recent_hands)
            if keep and recent_hands and hand_misses <= 8:
                recent_hands = [Box(**{**h.__dict__, "held": False}) for h in recent_hands]
                source = "held"
            else:
                recent_hands = []
                source = "miss"
        merged = tracks.assign(objs) + recent_hands
        prioritized = hand_priority(merged, fw, fh)
        person_shown = any(b.label.lower() == "person" for b in prioritized)
        locked = lock.update(prioritized, fw, fh)
        aim = aimed(prioritized, fw, fh, prefer_id=locked)
        lock_box = next((b for b in prioritized if b.track_id == locked), None)
        seg = segment_at(gt, t)
        hand_ok = (seg["hand"] != "pointing") or any(b.kind == "hand" for b in prioritized)
        target_ok = True
        steal = False
        if seg["target"] == "none":
            target_ok = lock_box is None or not lock_in_region(lock_box, [0.48, 0.62, 0.92, 0.96], fw, fh)
        else:
            target_ok = lock_in_region(lock_box, seg.get("region"), fw, fh)
            steal = lock_in_region(lock_box, [0.48, 0.62, 0.92, 0.96], fw, fh) and seg["target"] != "phone"
        ray_hit = ray_hits_region(aim, seg.get("region"), fw, fh) if seg["target"] != "none" else False
        label = lock_box.label if lock_box else None
        expected = SEMANTIC.get(seg["target"], set())
        if seg["target"] in ("none",) or label is None or label == "object":
            semantic = "unscored"
        elif label in expected:
            semantic = "correct"
        else:
            semantic = "wrong"
        hud = (
            f"hands={sum(1 for b in prioritized if b.kind=='hand')} src={source} "
            f"lock={lock_box.label if lock_box else '-'} gt={seg['target']} person={int(person_shown)}"
        )
        writer.write(draw(frame, prioritized, locked, aim, t, hud))
        rows.append(
            {
                "t": round(t, 3),
                "hand_source": source,
                "hands": sum(1 for b in prioritized if b.kind == "hand"),
                "person_fp": person_shown,
                "lock_label": lock_box.label if lock_box else None,
                "lock_id": locked,
                "ray": aim is not None,
                "hit": aim.hit.label if aim and aim.hit else None,
                "gt_hand": seg["hand"],
                "gt_target": seg["target"],
                "holdout": bool(seg.get("holdout")),
                "hand_ok": hand_ok,
                "target_ok": target_ok,
                "ray_hits_gt": ray_hit,
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
        "note": "arm_box is not a MediaPipe fingertip. semantic does not rename book to phone or cup to barrel. Zero locks fails.",
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
            "ray_hit": rate(lambda r: r["ray_hits_gt"], group),
        }
    metrics["per_target"] = per_target
    (out_dir / "frames.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    # Zero locks is a failure even when phone steal is also zero.
    fail = metrics["lock_frames"] == 0 or metrics["lock_recall"] <= 0 or metrics["phone_steal_rate_other_targets"] > 0.08
    return 1 if fail else 0


def ray_hits_region(aim, region, fw: int, fh: int) -> bool:
    if aim is None or not region:
        return False
    x0, y0, x1, y1 = region[0] * fw, region[1] * fh, region[2] * fw, region[3] * fh
    gt_box = Box("object", "region", 1.0, x0, y0, x1, y1, track_id=1)
    return entered(aim.ray, gt_box) is not None


def extended_ok(hand: Box, fw: int, fh: int) -> bool:
    from tracker_port import extended

    return extended(hand, fw, fh) is not None


def has_planar(hand: Box) -> bool:
    if len(hand.points) < 21:
        return False
    palm = ((hand.points[5][0] - hand.points[0][0]) ** 2 + (hand.points[5][1] - hand.points[0][1]) ** 2) ** 0.5
    if palm < 4:
        return False
    reach = ((hand.points[8][0] - hand.points[5][0]) ** 2 + (hand.points[8][1] - hand.points[5][1]) ** 2) ** 0.5
    return reach >= palm * 0.50


if __name__ == "__main__":
    sys.exit(main())
