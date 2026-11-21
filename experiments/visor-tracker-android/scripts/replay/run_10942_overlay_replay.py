#!/usr/bin/env python3
"""End-to-end replay and full overlay video renderer for 10942.mp4 (Issue #40, Commit 864d4e8).

Runs 10942.mp4 through the real production Visor tracking pipeline:
1. YOLO26n-seg for instance segmentation masks and 80 COCO object classes on the 1080x1440 camera viewport.
2. YOLO26-Depth ONNX engine for aligned metric depth maps.
3. MediaPipe HandLandmarker (21 3D joints) on the camera viewport.
4. Production Kotlin ProductionTracker & SpatialTargetingEngine via Gradle test driver.
5. Composites hand skeleton, prominent fingertip, forward pointing ray, instance segmentation mask polygons,
   detected objects, and target-lock highlights onto the original phone screen recording.
6. If the model cannot reliably identify a target, shows NO_TARGET instead of inventing one.
7. Encodes compatible H.264 video with original AAC audio preserved from 10942.mp4.
8. Saves output to 10942_overlay_replay_after_864d4e8.mp4.
9. Opens the resulting MP4 in the Windows video player.
10. Extracts before-and-after screenshots for the 16 pointing-reference keyframes.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

WORKTREE = Path(r"C:\Users\Admin\cloud-ai-os-visor-tracker")
MODULE = WORKTREE / "experiments" / "visor-tracker-android"
DEFAULT_VIDEO = Path(r"C:\Users\Admin\Downloads\10942.mp4")
DEFAULT_OUT_DIR = WORKTREE / "test-recordings" / "10942"
DEFAULT_OUT_MP4 = DEFAULT_OUT_DIR / "10942_overlay_replay_after_864d4e8.mp4"
MODELS = MODULE / "app" / "build" / "generated" / "visionAssets"
GRADLE_BAT = Path(r"C:\Users\Admin\AppData\Local\gradle-dist\gradle-9.6.0\bin\gradle.bat")

YOLO_SEG_MODEL = WORKTREE / "yolo26n-seg.pt"
YOLO_DEPTH_MODEL = WORKTREE / "yolo26n-depth.onnx"

HAND_SCORE = 0.15
OBJECT_SCORE = 0.12

VIEWPORT_TOP = 438
VIEWPORT_BOTTOM = 1878
CAM_W = 1080
CAM_H = 1440

WRIST, INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 0, 5, 6, 7, 8

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),        # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),        # index
    (5, 9), (9, 10), (10, 11), (11, 12),   # middle
    (9, 13), (13, 14), (14, 15), (15, 16), # ring
    (13, 17), (17, 18), (18, 19), (19, 20),# pinky
    (0, 17)                                # palm
]

COLOR_PALETTE = [
    (255, 140, 0),    # deep cyan/sky
    (0, 200, 255),    # bright yellow/gold
    (255, 100, 150),  # violet
    (50, 220, 255),   # orange
    (180, 255, 100),  # lime
    (255, 180, 180),  # lavender
    (100, 255, 200),  # spring green
    (200, 150, 255),  # magenta
]


class Box:
    def __init__(self, kind: str, label: str, score: float, left: float, top: float, right: float, bottom: float,
                 points: list[tuple[float, float]] = None, depths: list[float] = None, track_id: int = 0):
        self.kind = kind
        self.label = label
        self.score = score
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom
        self.points = points or []
        self.depths = depths or []
        self.track_id = track_id


def load_detectors():
    import onnxruntime as ort
    from ultralytics import YOLO
    from mediapipe.tasks.python.core.base_options import BaseOptions
    from mediapipe.tasks.python.vision import (
        HandLandmarker,
        HandLandmarkerOptions,
        RunningMode,
    )

    hand_model = MODELS / "hand_landmarker.task"
    if not hand_model.exists():
        raise SystemExit(f"Missing MediaPipe hand model in {MODELS}")

    print("Loading YOLO26n-seg model...", flush=True)
    yolo_model = YOLO(str(YOLO_SEG_MODEL))

    print("Loading YOLO26-Depth ONNX session...", flush=True)
    depth_sess = None
    if YOLO_DEPTH_MODEL.exists():
        depth_sess = ort.InferenceSession(str(YOLO_DEPTH_MODEL))
    else:
        print("Warning: yolo26n-depth.onnx not found, running without metric depth maps.")

    print("Loading MediaPipe HandLandmarker...", flush=True)
    hands = HandLandmarker.create_from_options(
        HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(hand_model)),
            running_mode=RunningMode.IMAGE,
            num_hands=2,
            min_hand_detection_confidence=HAND_SCORE,
            min_hand_presence_confidence=HAND_SCORE,
            min_tracking_confidence=HAND_SCORE,
        )
    )
    return yolo_model, depth_sess, hands


def estimate_depth_map(depth_sess, bgr_cam: np.ndarray) -> np.ndarray | None:
    if depth_sess is None:
        return None
    small = cv2.resize(bgr_cam, (640, 640))
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    nchw = np.transpose(rgb, (2, 0, 1))[np.newaxis, ...]
    inp_name = depth_sess.get_inputs()[0].name
    out = depth_sess.run(None, {inp_name: nchw})[0]
    return out[0, 0]


def detect_objects_yolo26(yolo_model, bgr_cam: np.ndarray, depth_map: np.ndarray | None) -> list[Box]:
    h, w = bgr_cam.shape[:2]
    res = yolo_model.predict(bgr_cam, conf=OBJECT_SCORE, verbose=False)[0]
    boxes = []

    if res.boxes is not None and len(res.boxes) > 0:
        masks_xy = res.masks.xy if res.masks is not None else [[] for _ in range(len(res.boxes))]
        for b, poly in zip(res.boxes, masks_xy):
            cls_id = int(b.cls[0])
            name = res.names[cls_id]
            score = float(b.conf[0])
            x0, y0, x1, y1 = [float(v) for v in b.xyxy[0]]

            # Sample polygon points (up to 48 points)
            poly_pts = []
            if len(poly) > 0:
                stride = max(1, len(poly) // 48)
                poly_pts = [(float(p[0]), float(p[1])) for p in poly[::stride]]

            # Extract median depth from depth map
            depths = []
            if depth_map is not None:
                dx0 = int(max(0, x0 / w * 640))
                dy0 = int(max(0, y0 / h * 640))
                dx1 = int(min(639, x1 / w * 640))
                dy1 = int(min(639, y1 / h * 640))
                if dx1 > dx0 and dy1 > dy0:
                    med_d = float(np.median(depth_map[dy0:dy1, dx0:dx1]))
                    depths = [med_d]

            boxes.append(
                Box(
                    kind="object",
                    label=name,
                    score=score,
                    left=x0,
                    top=y0,
                    right=x1,
                    bottom=y1,
                    points=poly_pts,
                    depths=depths,
                )
            )
    return boxes


def detect_hands(landmarker, bgr_cam: np.ndarray, depth_map: np.ndarray | None) -> list[Box]:
    import mediapipe as mp

    h, w = bgr_cam.shape[:2]
    rgb = cv2.cvtColor(bgr_cam, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = landmarker.detect(mp_image)
    boxes = []
    handed = result.handedness if hasattr(result, "handedness") and result.handedness else []
    for idx, lm in enumerate(result.hand_landmarks):
        if len(lm) < 21:
            continue
        score = 0.80
        if idx < len(handed) and len(handed[idx]) > 0:
            score = float(handed[idx][0].score)
        if score < HAND_SCORE:
            continue

        pts = [(p.x * w, p.y * h) for p in lm]
        
        # Depths: MediaPipe relative z, supplemented by metric depth map if present
        depths = [float(p.z * w) for p in lm]
        if depth_map is not None and len(pts) > 8:
            tip_x = int(max(0, min(639, pts[INDEX_TIP][0] / w * 640)))
            tip_y = int(max(0, min(639, pts[INDEX_TIP][1] / h * 640)))
            tip_metric_d = float(depth_map[tip_y, tip_x])
            depths[INDEX_TIP] = tip_metric_d

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


def write_detection_record(frame_record: dict) -> str:
    lines = [f"FRAME {frame_record['width']} {frame_record['height']} {frame_record['t']:.4f}"]
    for box in frame_record["boxes"]:
        label = box["label"].replace(" ", "\\u0020").replace("\"", "\\\"")
        lines.append(
            f"BOX {box['kind']} \"{label}\" {box['score']:.4f} {box['left']:.2f} {box['top']:.2f} {box['right']:.2f} {box['bottom']:.2f}"
        )
        if box.get("points"):
            flat = " ".join(f"{p[0]:.2f} {p[1]:.2f}" for p in box["points"])
            lines.append("POINTS " + flat)
        if box.get("depths"):
            flat_d = " ".join(f"{d:.4f}" for d in box["depths"])
            lines.append("DEPTHS " + flat_d)
    lines.append("END")
    return "\n".join(lines) + "\n"


def run_kotlin_tracker(out_dir: Path) -> None:
    if not GRADLE_BAT.exists():
        raise SystemExit(f"gradle.bat not found at {GRADLE_BAT}")
    env = os.environ.copy()
    env["ANDROID_HOME"] = r"C:\Users\Admin\AppData\Local\Android\Sdk"
    env["VISOR_REPLAY_DIR"] = str(out_dir)
    print("Executing ProductionTracker / SpatialTargetingEngine via Gradle test driver...", flush=True)
    cmd = [
        str(GRADLE_BAT),
        ":app:testDebugUnitTest",
        "--rerun-tasks",
        "--tests",
        "com.mattumali.visortracker.ReplayDriverTest",
        "--no-daemon",
    ]
    res = subprocess.run(cmd, cwd=MODULE, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        print("Gradle failed:\n" + res.stdout + "\n" + res.stderr)
        raise SystemExit("ProductionTracker replay execution failed")
    print("Production decisions generated successfully.", flush=True)


def draw_overlay(frame: np.ndarray, decision: dict, frame_idx: int, total_frames: int, t_sec: float) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]

    lock_id = decision.get("lock_id")
    hit_id = decision.get("hit_id")
    has_ray = decision.get("ray", False)
    targeting_state = decision.get("targeting_state", "NONE")
    state = decision.get("state", "NONE")
    boxes = decision.get("boxes", [])

    y_offset = VIEWPORT_TOP

    locked_label = None
    locked_depth = None
    hit_label = None
    hit_depth = None
    for b in boxes:
        if b.get("kind") == "object":
            if b.get("track_id") == lock_id and lock_id is not None:
                locked_label = b.get("label", "object")
                if b.get("depths"):
                    locked_depth = b["depths"][0]
            if b.get("track_id") == hit_id and hit_id is not None:
                hit_label = b.get("label", "object")
                if b.get("depths"):
                    hit_depth = b["depths"][0]

    # Overlay layer for translucent segmentation polygons
    poly_overlay = out.copy()
    has_poly = False

    # 1. Draw segmentation polygons & detected objects
    for b in boxes:
        kind = b.get("kind")
        label = b.get("label", "")
        tid = b.get("track_id", 0)
        score = b.get("score", 0.0)
        depth_val = b.get("depths", [None])[0] if b.get("depths") else None

        x0 = int(b["left"])
        y0 = int(b["top"] + y_offset)
        x1 = int(b["right"])
        y1 = int(b["bottom"] + y_offset)

        if kind == "object":
            is_locked = (tid == lock_id and lock_id is not None)
            is_hit = (tid == hit_id and hit_id is not None)

            # Palette color based on track_id
            pal_color = COLOR_PALETTE[tid % len(COLOR_PALETTE)]

            # Draw instance segmentation mask polygon if points exist
            pts = b.get("points")
            if pts and len(pts) >= 3:
                has_poly = True
                poly_scr = np.array([[int(p[0]), int(p[1] + y_offset)] for p in pts], dtype=np.int32)
                mask_color = (0, 255, 60) if is_locked else ((0, 220, 255) if is_hit else pal_color)
                # Fill polygon on overlay layer
                cv2.fillPoly(poly_overlay, [poly_scr], mask_color)
                # Outline contour
                cv2.polylines(out, [poly_scr], isClosed=True, color=mask_color, thickness=2, lineType=cv2.LINE_AA)

            depth_str = f" ~{depth_val:.2f}m" if (depth_val is not None and depth_val > 0) else ""

            if is_locked:
                color = (0, 255, 60)  # Neon Green
                thick = 4
                tag = f"LOCKED #{tid} {label.upper()} ({score:.2f}){depth_str}"
                
                # Corner bracket HUD styling
                cv2.rectangle(out, (x0, y0), (x1, y1), color, thick)
                cw = max(16, (x1 - x0) // 5)
                ch = max(16, (y1 - y0) // 5)
                for cx, cy in [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]:
                    dx = 1 if cx == x0 else -1
                    dy = 1 if cy == y0 else -1
                    cv2.line(out, (cx, cy), (cx + dx * cw, cy), (255, 255, 255), 4)
                    cv2.line(out, (cx, cy), (cx, cy + dy * ch), (255, 255, 255), 4)

                # Tag background
                tw, th = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.70, 2)[0]
                ty = max(y_offset + 30, y0 - 10)
                cv2.rectangle(out, (x0, ty - th - 6), (x0 + tw + 14, ty + 4), (0, 160, 40), -1)
                cv2.putText(out, tag, (x0 + 6, ty - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (255, 255, 255), 2, cv2.LINE_AA)

            elif is_hit:
                color = (0, 220, 255)  # Bright Gold / Yellow
                thick = 3
                tag = f"HOVER #{tid} {label} ({score:.2f}){depth_str}"
                cv2.rectangle(out, (x0, y0), (x1, y1), color, thick)
                tw, th = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)[0]
                ty = max(y_offset + 30, y0 - 8)
                cv2.rectangle(out, (x0, ty - th - 4), (x0 + tw + 10, ty + 2), (0, 140, 180), -1)
                cv2.putText(out, tag, (x0 + 5, ty - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)

            elif label.lower() == "person":
                color = (100, 100, 100)
                tag = f"arm#{tid}"
                cv2.rectangle(out, (x0, y0), (x1, y1), color, 1)
                cv2.putText(out, tag, (x0, max(y_offset + 20, y0 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
            else:
                color = pal_color
                tag = f"#{tid} {label} ({score:.2f}){depth_str}"
                cv2.rectangle(out, (x0, y0), (x1, y1), color, 2)
                tw, th = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)[0]
                ty = max(y_offset + 24, y0 - 6)
                cv2.rectangle(out, (x0, ty - th - 4), (x0 + tw + 8, ty + 2), (20, 20, 20), -1)
                cv2.putText(out, tag, (x0 + 4, ty - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)

        elif kind == "hand" and b.get("points"):
            pts = b["points"]
            # Skeleton bones
            for p1_idx, p2_idx in HAND_CONNECTIONS:
                if p1_idx < len(pts) and p2_idx < len(pts):
                    pt1 = (int(pts[p1_idx][0]), int(pts[p1_idx][1] + y_offset))
                    pt2 = (int(pts[p2_idx][0]), int(pts[p2_idx][1] + y_offset))
                    cv2.line(out, pt1, pt2, (255, 220, 0), 2, cv2.LINE_AA)

            # Joints
            for p in pts:
                cv2.circle(out, (int(p[0]), int(p[1] + y_offset)), 4, (0, 255, 255), -1)

            # Prominent fingertip on index tip (joint 8)
            tip = pts[8]
            tip_scr = (int(tip[0]), int(tip[1] + y_offset))
            cv2.circle(out, tip_scr, 14, (0, 240, 255), 3, cv2.LINE_AA)
            cv2.circle(out, tip_scr, 7, (0, 0, 255), -1, cv2.LINE_AA)
            cv2.circle(out, tip_scr, 2, (255, 255, 255), -1, cv2.LINE_AA)

    # Blend polygon overlays
    if has_poly:
        cv2.addWeighted(poly_overlay, 0.22, out, 0.78, 0, out)

    # 2. Draw forward pointing ray
    if has_ray:
        tip_x = decision.get("tip_x", 0.0)
        tip_y = decision.get("tip_y", 0.0) + y_offset
        dir_x = decision.get("dir_x", 0.0)
        dir_y = decision.get("dir_y", 0.0)
        reach = decision.get("reach", 1200.0)

        ray_len = min(reach, max(CAM_W, CAM_H) * 0.85)
        end_x = int(tip_x + dir_x * ray_len)
        end_y = int(tip_y + dir_y * ray_len)

        ray_color = (0, 255, 100) if lock_id is not None else ((0, 220, 255) if hit_id is not None else (0, 160, 255))
        cv2.line(out, (int(tip_x), int(tip_y)), (end_x, end_y), ray_color, 4, cv2.LINE_AA)
        cv2.line(out, (int(tip_x), int(tip_y)), (end_x, end_y), (255, 255, 255), 1, cv2.LINE_AA)
        cv2.circle(out, (int(tip_x), int(tip_y)), 7, ray_color, -1, cv2.LINE_AA)
        cv2.circle(out, (end_x, end_y), 5, (255, 255, 255), -1, cv2.LINE_AA)

    # 3. Top HUD Banner (rows 50 to 195)
    top_bar_y0 = 50
    top_bar_h = 145
    cv2.rectangle(out, (20, top_bar_y0), (w - 20, top_bar_y0 + top_bar_h), (20, 20, 24), -1)
    cv2.rectangle(out, (20, top_bar_y0), (w - 20, top_bar_y0 + top_bar_h), (70, 70, 80), 2)

    # Header title + timing
    header_title = "VISOR SPATIAL TARGETING REPLAY — YOLO26n-seg + DEPTH"
    time_str = f"t={t_sec:5.2f}s | Frame {frame_idx:04d}/{total_frames}"
    cv2.putText(out, header_title, (40, top_bar_y0 + 35), cv2.FONT_HERSHEY_SIMPLEX, 0.80, (0, 240, 255), 2, cv2.LINE_AA)
    cv2.putText(out, time_str, (w - 410, top_bar_y0 + 35), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (200, 200, 200), 2, cv2.LINE_AA)

    # State
    state_str = f"STATE: {targeting_state}"
    cv2.putText(out, state_str, (40, top_bar_y0 + 78), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)

    # Telemetry
    telemetry_str = "DETECTOR: YOLO26n-seg | DEPTH: YOLO26-Depth | HAND: MediaPipe 3D"
    cv2.putText(out, telemetry_str, (w - 580, top_bar_y0 + 78), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (160, 220, 160), 1, cv2.LINE_AA)

    # Target Badge
    if lock_id is not None:
        depth_suffix = f" (~{locked_depth:.2f}m)" if locked_depth else ""
        target_badge = f"LOCKED: #{lock_id} {locked_label.upper() if locked_label else 'TARGET'}{depth_suffix}"
        badge_bg = (0, 160, 40)
        badge_fg = (255, 255, 255)
    elif hit_id is not None:
        depth_suffix = f" (~{hit_depth:.2f}m)" if hit_depth else ""
        target_badge = f"AIMING: #{hit_id} {hit_label if hit_label else 'CANDIDATE'}{depth_suffix}"
        badge_bg = (0, 140, 200)
        badge_fg = (255, 255, 255)
    elif has_ray:
        target_badge = "NO_TARGET (AWAITING OBJECT)"
        badge_bg = (30, 80, 200)
        badge_fg = (255, 255, 255)
    else:
        target_badge = "STANDBY (SEARCHING)"
        badge_bg = (50, 50, 55)
        badge_fg = (180, 180, 180)

    bw, bh = cv2.getTextSize(target_badge, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)[0]
    bx = 40
    by = top_bar_y0 + 124
    cv2.rectangle(out, (bx, by - bh - 6), (bx + bw + 20, by + 6), badge_bg, -1)
    cv2.rectangle(out, (bx, by - bh - 6), (bx + bw + 20, by + 6), (255, 255, 255), 1)
    cv2.putText(out, target_badge, (bx + 10, by - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.75, badge_fg, 2, cv2.LINE_AA)

    # Ray status
    ray_label = "RAY: ACTIVE (FORWARD)" if has_ray else "RAY: INACTIVE"
    ray_color = (0, 255, 100) if has_ray else (140, 140, 140)
    cv2.putText(out, ray_label, (w - 410, top_bar_y0 + 124), cv2.FONT_HERSHEY_SIMPLEX, 0.70, ray_color, 2, cv2.LINE_AA)

    # Viewport Boundary Lines
    cv2.line(out, (0, VIEWPORT_TOP), (w, VIEWPORT_TOP), (50, 50, 60), 1)
    cv2.line(out, (0, VIEWPORT_BOTTOM), (w, VIEWPORT_BOTTOM), (50, 50, 60), 1)

    # Bottom status in bottom black bar (rows 1940 to 2050)
    bot_y0 = 1940
    bot_h = 100
    cv2.rectangle(out, (20, bot_y0), (w - 20, bot_y0 + bot_h), (20, 20, 24), -1)
    cv2.rectangle(out, (20, bot_y0), (w - 20, bot_y0 + bot_h), (70, 70, 80), 2)
    cv2.putText(out, "YOLO26n-seg (80 Classes + 32 Mask Prototypes) | YOLO26-Depth Metric Engine", (40, bot_y0 + 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (180, 180, 180), 1, cv2.LINE_AA)
    cv2.putText(out, "SpatialTargetingEngine (Kotlin) | Ray-Mask Intersection & Depth Gate", (40, bot_y0 + 75),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 220, 255), 1, cv2.LINE_AA)

    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--out-mp4", type=Path, default=DEFAULT_OUT_MP4)
    parser.add_argument("--skip-detect", action="store_true")
    parser.add_argument("--skip-kotlin", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    comp_dir = args.out_dir / "comparisons"
    comp_dir.mkdir(parents=True, exist_ok=True)

    det_file = args.out_dir / "detections.txt"
    dec_file = args.out_dir / "decisions.jsonl"

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video: {args.video}")

    total_video_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.28
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"Loaded input video: {width}x{height}, {total_video_frames} frames @ {fps:.2f} fps", flush=True)

    limit = args.limit if args.limit else total_video_frames

    # Phase 1: Inference & Detection Extraction with YOLO26n-seg, YOLO26-Depth, MediaPipe
    if not args.skip_detect or not det_file.exists():
        print(f"--- Phase 1: Running YOLO26n-seg, YOLO26-Depth, and MediaPipe on {limit} frames ---", flush=True)
        yolo_model, depth_sess, hands_det = load_detectors()

        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        t0 = time.time()
        with open(det_file, "w", encoding="utf-8") as f_out:
            for i in range(limit):
                ret, frame = cap.read()
                if not ret:
                    break
                t_sec = i / fps
                cam_bgr = frame[VIEWPORT_TOP:VIEWPORT_BOTTOM, :]

                # Estimate aligned metric depth map (updated at 15 Hz matching production VisionPipeline depthExecutor)
                if i % 2 == 0 or depth_map is None:
                    depth_map = estimate_depth_map(depth_sess, cam_bgr)

                # Detect hands on camera frame with 3D landmarks & metric tip depth
                hand_boxes = detect_hands(hands_det, cam_bgr, depth_map)

                # Detect objects with YOLO26n-seg instance segmentation & depth
                obj_boxes = detect_objects_yolo26(yolo_model, cam_bgr, depth_map)

                frame_record = {
                    "width": CAM_W,
                    "height": CAM_H,
                    "t": t_sec,
                    "boxes": [
                        {
                            "kind": b.kind,
                            "label": b.label,
                            "score": b.score,
                            "left": b.left,
                            "top": b.top,
                            "right": b.right,
                            "bottom": b.bottom,
                            "points": b.points,
                            "depths": b.depths,
                        }
                        for b in (hand_boxes + obj_boxes)
                    ],
                }
                f_out.write(write_detection_record(frame_record))

                if (i + 1) % 50 == 0 or (i + 1) == limit:
                    elapsed = time.time() - t0
                    rate = (i + 1) / max(0.01, elapsed)
                    eta = (limit - (i + 1)) / max(0.01, rate)
                    print(f"Inference: {i + 1}/{limit} frames ({rate:.1f} fps, ETA {eta:.1f}s)...", flush=True)

        print(f"Phase 1 complete: wrote detections to {det_file}", flush=True)
    else:
        print(f"Using cached detections from {det_file}", flush=True)

    # Phase 2: Run Kotlin ProductionTracker / SpatialTargetingEngine
    if not args.skip_kotlin or not dec_file.exists():
        print("--- Phase 2: Running Kotlin ProductionTracker / SpatialTargetingEngine ---", flush=True)
        run_kotlin_tracker(args.out_dir)
        print(f"Phase 2 complete: decisions written to {dec_file}", flush=True)
    else:
        print(f"Using cached decisions from {dec_file}", flush=True)

    # Read decisions
    print("Loading production decisions...", flush=True)
    decisions = []
    with open(dec_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                decisions.append(json.loads(line))
    print(f"Loaded {len(decisions)} decision steps.", flush=True)

    # Keyframe timestamps for before-and-after screenshots
    key_timestamps = [
        0.8, 6.0, 10.2, 11.5, 12.5, 14.8, 15.5, 16.2,
        22.0, 24.2, 26.0, 27.5, 31.2, 36.7, 39.5, 40.2
    ]
    key_frame_indices = set(int(round(ts * fps)) for ts in key_timestamps)

    # Phase 3: Composite Overlay & Render Video with Audio Muxing
    print("--- Phase 3: Compositing overlays and encoding H.264 video with AAC audio ---", flush=True)
    temp_raw_mp4 = args.out_dir / "temp_video_only.mp4"

    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{width}x{height}",
        "-pix_fmt", "bgr24",
        "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "fast",
        "-crf", "19",
        str(temp_raw_mp4),
    ]

    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    t0 = time.time()
    n_frames = min(limit, len(decisions))
    for i in range(n_frames):
        ret, frame = cap.read()
        if not ret:
            break
        t_sec = i / fps
        dec = decisions[i]

        annotated = draw_overlay(frame, dec, i + 1, n_frames, t_sec)
        proc.stdin.write(annotated.tobytes())

        # Save before-and-after comparison screenshot on reference keyframes
        if i in key_frame_indices:
            orig_crop = frame[VIEWPORT_TOP:VIEWPORT_BOTTOM, :]
            ann_crop = annotated[VIEWPORT_TOP:VIEWPORT_BOTTOM, :]
            side_by_side = np.hstack([orig_crop, ann_crop])
            cv2.putText(side_by_side, f"ORIGINAL (t={t_sec:4.1f}s)", (30, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(side_by_side, f"YOLO26n-seg + SPATIAL ENGINE REPLAY (t={t_sec:4.1f}s)", (CAM_W + 30, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 240, 255), 2, cv2.LINE_AA)
            out_img_path = comp_dir / f"compare_frame_{t_sec:04.1f}s.jpg"
            cv2.imwrite(str(out_img_path), side_by_side, [cv2.IMWRITE_JPEG_QUALITY, 90])

        if (i + 1) % 100 == 0 or (i + 1) == n_frames:
            elapsed = time.time() - t0
            rate = (i + 1) / max(0.01, elapsed)
            print(f"Rendered {i + 1}/{n_frames} frames ({rate:.1f} fps)...", flush=True)

    proc.stdin.flush()
    _, stderr_bytes = proc.communicate()
    cap.release()

    if proc.returncode != 0:
        err = stderr_bytes.decode("utf-8", errors="replace")
        raise SystemExit(f"FFmpeg video encoding failed:\n{err}")

    # Phase 4: Mux original AAC audio from 10942.mp4 into final MP4
    print("Muxing original audio stream into final MP4...", flush=True)
    mux_cmd = [
        "ffmpeg",
        "-y",
        "-i", str(temp_raw_mp4),
        "-i", str(args.video),
        "-map", "0:v:0",
        "-map", "1:a:0?",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(args.out_mp4),
    ]
    res_mux = subprocess.run(mux_cmd, capture_output=True, text=True)
    if res_mux.returncode != 0:
        print("Audio muxing fallback to video-only if no audio stream:\n" + res_mux.stderr)
        shutil.copy2(temp_raw_mp4, args.out_mp4)
    else:
        if temp_raw_mp4.exists():
            temp_raw_mp4.unlink()

    # Also copy to 10942_overlay_replay.mp4 for convenience
    replay_standard = args.out_dir / "10942_overlay_replay.mp4"
    shutil.copy2(args.out_mp4, replay_standard)

    print(f"\n=======================================================")
    print(f"Replay video successfully created at:\n{args.out_mp4}")
    print(f"Size: {args.out_mp4.stat().st_size / (1024*1024):.2f} MB")
    print(f"=======================================================\n", flush=True)

    # Launch video player in Windows
    print("Launching video player...", flush=True)
    subprocess.run(["powershell", "-Command", f"Start-Process '{args.out_mp4}'"], check=False)
    print("Video player launched.", flush=True)


if __name__ == "__main__":
    main()
