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

YOLOE_ONNX = WORKTREE / "yoloe-26n-seg.onnx"
YOLO_SEG_ONNX = YOLOE_ONNX if YOLOE_ONNX.exists() else (WORKTREE / "yolo26n-seg.onnx")
YOLO_DEPTH_MODEL = WORKTREE / "yolo26n-depth.onnx"

YOLOE_CLASSES = [
    "clothes hamper", "hanging clothes", "portable air conditioner",
    "laptop", "white plastic trash bag", "mouse", "bottle", "person"
]

YOLOE_CLASSES_12 = [
    "laundry basket", "closet clothing", "portable air conditioner",
    "windowsill", "conical hat", "laptop", "paper roll",
    "white plastic-covered item", "trash bag", "mouse", "bottle", "person"
]

COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator",
    "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"
]

HAND_SCORE = 0.15
HAND_MODEL_THRESHOLD = 0.05
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
    from mediapipe.tasks.python.core.base_options import BaseOptions
    from mediapipe.tasks.python.vision import (
        HandLandmarker,
        HandLandmarkerOptions,
        RunningMode,
    )

    hand_model = MODELS / "hand_landmarker.task"
    if not hand_model.exists():
        raise SystemExit(f"Missing MediaPipe hand model in {MODELS}")

    if not YOLO_SEG_ONNX.exists():
        raise SystemExit(f"Missing YOLO26n-seg ONNX model at {YOLO_SEG_ONNX}")

    print("Loading YOLO26n-seg ONNX session (production pipeline)...", flush=True)
    yolo_sess = ort.InferenceSession(str(YOLO_SEG_ONNX))

    print("Loading YOLO26-Depth ONNX session...", flush=True)
    depth_sess = None
    if YOLO_DEPTH_MODEL.exists():
        depth_sess = ort.InferenceSession(str(YOLO_DEPTH_MODEL))
    else:
        print("Warning: yolo26n-depth.onnx not found, running without relative depth maps.")

    print("Loading MediaPipe HandLandmarker...", flush=True)
    hands = HandLandmarker.create_from_options(
        HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(hand_model)),
            running_mode=RunningMode.VIDEO,
            num_hands=2,
            min_hand_detection_confidence=HAND_MODEL_THRESHOLD,
            min_hand_presence_confidence=HAND_MODEL_THRESHOLD,
            min_tracking_confidence=HAND_MODEL_THRESHOLD,
        )
    )
    return yolo_sess, depth_sess, hands


def estimate_depth_map(depth_sess, bgr_cam: np.ndarray) -> np.ndarray | None:
    if depth_sess is None:
        return None
    small = cv2.resize(bgr_cam, (640, 640))
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    nchw = np.transpose(rgb, (2, 0, 1))[np.newaxis, ...]
    inp_name = depth_sess.get_inputs()[0].name
    out = depth_sess.run(None, {inp_name: nchw})[0]
    return out[0, 0]


def extract_radial_mask_contour(
    x0: float, y0: float, x1: float, y1: float,
    mask_coeffs: np.ndarray, proto_masks: np.ndarray,
    orig_w: int, orig_h: int,
    scale: float, pad_x: int, pad_y: int
) -> list[tuple[float, float]]:
    px0 = max(0, int((x0 * scale + pad_x) / 4.0))
    py0 = max(0, int((y0 * scale + pad_y) / 4.0))
    px1 = min(159, int((x1 * scale + pad_x) / 4.0))
    py1 = min(159, int((y1 * scale + pad_y) / 4.0))

    if px1 <= px0 or py1 <= py0:
        return []

    step_x = max(1, (px1 - px0) // 20)
    step_y = max(1, (py1 - py0) // 20)
    grid_w = ((px1 - px0) // step_x) + 1
    grid_h = ((py1 - py0) // step_y) + 1

    mask_grid = np.zeros((grid_h, grid_w), dtype=bool)
    sum_x, sum_y, count = 0.0, 0.0, 0

    for gy in range(grid_h):
        py = min(159, py0 + gy * step_y)
        for gx in range(grid_w):
            px = min(159, px0 + gx * step_x)
            dot = float(np.dot(mask_coeffs, proto_masks[:, py, px]))
            is_occ = (1.0 / (1.0 + math.exp(-dot))) > 0.5
            mask_grid[gy, gx] = is_occ
            if is_occ:
                fx = max(0.0, min(float(orig_w), (px * 4.0 - pad_x) / scale))
                fy = max(0.0, min(float(orig_h), (py * 4.0 - pad_y) / scale))
                sum_x += fx
                sum_y += fy
                count += 1

    if count < 3:
        return []

    cx = sum_x / count
    cy = sum_y / count

    boundary_points = []
    for gy in range(grid_h):
        py = min(159, py0 + gy * step_y)
        for gx in range(grid_w):
            if not mask_grid[gy, gx]:
                continue
            is_b = (
                gx == 0 or gx == grid_w - 1 or gy == 0 or gy == grid_h - 1 or
                not mask_grid[gy - 1, gx] or not mask_grid[gy + 1, gx] or
                not mask_grid[gy, gx - 1] or not mask_grid[gy, gx + 1]
            )
            if is_b:
                px = min(159, px0 + gx * step_x)
                fx = max(0.0, min(float(orig_w), (px * 4.0 - pad_x) / scale))
                fy = max(0.0, min(float(orig_h), (py * 4.0 - pad_y) / scale))
                boundary_points.append((fx, fy))

    if len(boundary_points) < 3:
        return []

    boundary_points.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    return boundary_points


def detect_objects_yolo26(yolo_sess, bgr_cam: np.ndarray, depth_map: np.ndarray | None) -> list[Box]:
    h, w = bgr_cam.shape[:2]

    # Letterbox to 640x640 (exact Android LetterboxTransform)
    scale = min(640.0 / w, 640.0 / h)
    sw, sh = int(w * scale), int(h * scale)
    pad_x = (640 - sw) // 2
    pad_y = (640 - sh) // 2

    resized = cv2.resize(bgr_cam, (sw, sh))
    canvas = np.zeros((640, 640, 3), dtype=np.uint8)
    canvas[pad_y:pad_y + sh, pad_x:pad_x + sw] = resized
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    nchw = np.transpose(rgb, (2, 0, 1))[np.newaxis, ...]

    inp_name = yolo_sess.get_inputs()[0].name
    out0, out1 = yolo_sess.run(None, {inp_name: nchw})
    out0 = out0[0]  # [channels, 8400]
    out1 = out1[0]  # [32, 160, 160]

    num_channels = out0.shape[0]
    num_classes = num_channels - 4 - 32
    if num_classes == 8:
        class_names = YOLOE_CLASSES
    elif num_classes == 12:
        class_names = YOLOE_CLASSES_12
    elif num_classes == 80:
        class_names = COCO_CLASSES
    else:
        class_names = COCO_CLASSES[:num_classes]

    # Parse 8400 anchor outputs
    raw_boxes = []
    for i in range(8400):
        scores = out0[4 : 4 + num_classes, i]
        max_c = int(np.argmax(scores))
        score = float(scores[max_c])
        if score >= OBJECT_SCORE and max_c < len(class_names):
            cx, cy, bw, bh = out0[0:4, i]
            x0 = max(0.0, min(float(w), (cx - bw / 2.0 - pad_x) / scale))
            y0 = max(0.0, min(float(h), (cy - bh / 2.0 - pad_y) / scale))
            x1 = max(0.0, min(float(w), (cx + bw / 2.0 - pad_x) / scale))
            y1 = max(0.0, min(float(h), (cy + bh / 2.0 - pad_y) / scale))
            raw_boxes.append((max_c, score, x0, y0, x1, y1, out0[4 + num_classes : 4 + num_classes + 32, i]))

    # Class-aware NMS (Failure Mode 12)
    raw_boxes.sort(key=lambda b: b[1], reverse=True)
    nms_boxes = []
    for b in raw_boxes:
        overlap = False
        area = max(0.0, b[4] - b[2]) * max(0.0, b[5] - b[3])
        for c in nms_boxes:
            ix0 = max(b[2], c[2])
            iy0 = max(b[3], c[3])
            ix1 = min(b[4], c[4])
            iy1 = min(b[5], c[5])
            ia = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
            c_area = max(0.0, c[4] - c[2]) * max(0.0, c[5] - c[3])
            union = area + c_area - ia
            iou_val = (ia / union) if union > 0 else 0.0
            if c[0] == b[0] and iou_val > 0.45:
                overlap = True
                break
            if c[0] != b[0] and iou_val > 0.60:
                overlap = True
                break
        if not overlap:
            nms_boxes.append(b)

    boxes = []
    for cls_id, score, x0, y0, x1, y1, coeffs in nms_boxes:
        label = class_names[cls_id]

        # Extract ordered radial boundary contour (Failure Mode 11)
        poly_pts = extract_radial_mask_contour(x0, y0, x1, y1, coeffs, out1, w, h, scale, pad_x, pad_y)

        # Relative depth from depth map
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
                label=label,
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


def detect_hands(
    landmarker,
    bgr_cam: np.ndarray,
    depth_map: np.ndarray | None,
    timestamp_ms: int,
    prev_hand: Box | None = None,
) -> list[Box]:
    import mediapipe as mp

    h, w = bgr_cam.shape[:2]
    rgb = cv2.cvtColor(bgr_cam, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = landmarker.detect_for_video(mp_image, timestamp_ms)

    # Secondary detection attempt on lost frames using expanded ROI around previous hand
    if len(result.hand_landmarks) == 0 and prev_hand is not None:
        pw = prev_hand.right - prev_hand.left
        ph = prev_hand.bottom - prev_hand.top
        if pw > 10 and ph > 10:
            cx = (prev_hand.left + prev_hand.right) / 2.0
            cy = (prev_hand.top + prev_hand.bottom) / 2.0
            roi_w = max(pw * 1.6, 200.0)
            roi_h = max(ph * 1.6, 200.0)
            rx0 = int(max(0, cx - roi_w / 2.0))
            ry0 = int(max(0, cy - roi_h / 2.0))
            rx1 = int(min(w, cx + roi_w / 2.0))
            ry1 = int(min(h, cy + roi_h / 2.0))
            if rx1 > rx0 + 40 and ry1 > ry0 + 40:
                crop = rgb[ry0:ry1, rx0:rx1]
                ch, cw = crop.shape[:2]
                mp_crop = mp.Image(image_format=mp.ImageFormat.SRGB, data=crop)
                crop_res = landmarker.detect_for_video(mp_crop, timestamp_ms + 1)
                if len(crop_res.hand_landmarks) > 0:
                    class ProxyLM:
                        def __init__(self, x, y, z):
                            self.x = x
                            self.y = y
                            self.z = z
                    remapped = []
                    for clm in crop_res.hand_landmarks:
                        remapped.append([
                            ProxyLM(
                                (rx0 + p.x * cw) / float(w),
                                (ry0 + p.y * ch) / float(h),
                                p.z * (cw / float(w)),
                            )
                            for p in clm
                        ])
                    result.hand_landmarks = remapped
                    if hasattr(crop_res, "handedness"):
                        result.handedness = crop_res.handedness

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
        
        # MediaPipe relative z is hand-relative geometry. Never replace a single
        # joint with scene-depth output: mixing those coordinate systems corrupts
        # the pointing ray.
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

            depth_str = ""

            if is_locked:
                color = (0, 255, 60)  # Neon Green
                thick = 4
                tag = f"LOCKED #{tid} {label.upper()} ({score:.2f})"
                
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
                tag = f"HOVER #{tid} {label} ({score:.2f})"
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
                tag = f"#{tid} {label} ({score:.2f})"
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
    header_title = "VISOR SPATIAL TARGETING REPLAY — PRODUCTION ONNX PIPELINE"
    time_str = f"t={t_sec:5.2f}s | Frame {frame_idx:04d}/{total_frames}"
    cv2.putText(out, header_title, (40, top_bar_y0 + 35), cv2.FONT_HERSHEY_SIMPLEX, 0.80, (0, 240, 255), 2, cv2.LINE_AA)
    cv2.putText(out, time_str, (w - 410, top_bar_y0 + 35), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (200, 200, 200), 2, cv2.LINE_AA)

    # State
    state_str = f"STATE: {targeting_state}"
    cv2.putText(out, state_str, (40, top_bar_y0 + 78), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)

    # Telemetry
    telemetry_str = "DETECTOR: YOLO26n-seg ONNX | HAND: MediaPipe 3D (Authoritative Landmark 8)"
    cv2.putText(out, telemetry_str, (w - 680, top_bar_y0 + 78), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (160, 220, 160), 1, cv2.LINE_AA)

    # Target Badge
    if lock_id is not None:
        target_badge = f"LOCKED: #{lock_id} {locked_label.upper() if locked_label else 'TARGET'}"
        badge_bg = (0, 160, 40)
        badge_fg = (255, 255, 255)
    elif hit_id is not None:
        target_badge = f"AIMING: #{hit_id} {hit_label if hit_label else 'CANDIDATE'}"
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
    cv2.putText(out, "2D Pointing with Relative-Depth Evidence (Authoritative Index Landmark 8 | Single Lock Authority)", (40, bot_y0 + 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.60, (180, 180, 180), 1, cv2.LINE_AA)
    cv2.putText(out, "Production Kotlin SpatialTargetingEngine Replay | Class-Aware NMS | Radial Boundary Contour", (40, bot_y0 + 75),
                cv2.FONT_HERSHEY_SIMPLEX, 0.60, (0, 220, 255), 1, cv2.LINE_AA)

    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--out-mp4", type=Path, default=DEFAULT_OUT_MP4)
    parser.add_argument("--skip-detect", action="store_true")
    parser.add_argument("--skip-kotlin", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-launch", action="store_true")
    args = parser.parse_args()

    args.video = args.video.resolve()
    args.out_dir = args.out_dir.resolve()
    args.out_mp4 = args.out_mp4.resolve()

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
    if width != CAM_W or height < VIEWPORT_BOTTOM:
        raise SystemExit(
            f"Unexpected recording geometry {width}x{height}; refusing to apply the "
            f"verified camera viewport x=0..{CAM_W - 1}, y={VIEWPORT_TOP}..{VIEWPORT_BOTTOM - 1}."
        )

    limit = args.limit if args.limit else total_video_frames

    # Phase 1: Inference & Detection Extraction with YOLO26n-seg, YOLO26-Depth, MediaPipe
    if not args.skip_detect or not det_file.exists():
        print(f"--- Phase 1: Running YOLO26n-seg, YOLO26-Depth, and MediaPipe on {limit} frames ---", flush=True)
        yolo_model, depth_sess, hands_det = load_detectors()
        depth_map = None

        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        t0 = time.time()
        last_hand_box = None
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
                hand_boxes = detect_hands(hands_det, cam_bgr, depth_map, int(round(t_sec * 1000)), prev_hand=last_hand_box)
                last_hand_box = hand_boxes[0] if hand_boxes else None

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
        0.8, 3.0, 5.1, 7.0, 9.5, 10.8, 12.0, 15.5, 18.0, 24.0,
        26.0, 27.5, 31.2, 36.7, 39.5, 40.2,
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

    if not args.no_launch:
        # Launch video player in Windows
        print("Launching video player...", flush=True)
        subprocess.run(["powershell", "-Command", f"Start-Process '{args.out_mp4}'"], check=False)
        print("Video player launched.", flush=True)


if __name__ == "__main__":
    main()
