#!/usr/bin/env python3
"""
Exports continuous per-pixel depth tensors from yolo26n-depth.onnx
for 10942 and 10917 replays into depth_frames.bin.
Format:
For each frame:
  seq: int64 (8 bytes, little-endian)
  capturedAtMs: int64 (8 bytes, little-endian)
  width: int32 (4 bytes, little-endian)
  height: int32 (4 bytes, little-endian)
  depthData: width * height float32 (little-endian)
"""

import sys
import os
import struct
import time
import cv2
import numpy as np
import onnxruntime as ort

MODEL_PATH = r"C:\Users\Admin\cloud-ai-os-visor-tracker\yolo26n-depth.onnx"

def export_depth(video_path, detections_path, output_bin_path, crop_y_range=None, grid_w=120, grid_h=160):
    print(f"\n=== Exporting Authentic Depth for {video_path} ===")
    print(f"Detections: {detections_path}")
    print(f"Output: {output_bin_path}")
    print(f"Grid size: {grid_w}x{grid_h}")

    session = ort.InferenceSession(MODEL_PATH)
    input_name = session.get_inputs()[0].name

    cap = cv2.VideoCapture(video_path)
    total_video_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"Video open: {total_video_frames} frames, {fps:.2f} FPS")

    # Read detection frames timestamps
    det_timestamps = []
    with open(detections_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('FRAME'):
                parts = line.strip().split()
                # FRAME width height timestamp
                det_timestamps.append(float(parts[3]))

    print(f"Detections frame count: {len(det_timestamps)}")
    os.makedirs(os.path.dirname(output_bin_path), exist_ok=True)

    written_frames = 0
    t0 = time.time()

    with open(output_bin_path, 'wb') as out_f:
        for idx, t_sec in enumerate(det_timestamps):
            # Calculate video frame index
            if crop_y_range is not None:
                # 10942 consecutive frames
                frame_idx = idx
            else:
                # 10917 sampled by timestamp
                frame_idx = int(round(t_sec * fps))

            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                print(f"Warning: could not read frame {frame_idx} (t={t_sec:.3f}s), using blank")
                depth_resized = np.full((grid_h, grid_w), 3.0, dtype=np.float32)
            else:
                if crop_y_range is not None:
                    y0, y1 = crop_y_range
                    frame = frame[y0:y1, :]

                # Preprocess for yolo26n-depth.onnx (640x640 RGB float32 [0, 1])
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                small = cv2.resize(rgb, (640, 640)).astype(np.float32) / 255.0
                inp = np.transpose(small, (2, 0, 1))[np.newaxis, ...]

                # Run inference
                raw_depth = session.run(None, {input_name: inp})[0][0, 0] # 640x640 float32

                # Resize to grid (grid_w columns, grid_h rows)
                depth_resized = cv2.resize(raw_depth, (grid_w, grid_h), interpolation=cv2.INTER_AREA).astype(np.float32)

            captured_at_ms = int(t_sec * 1000)
            header = struct.pack('<qqii', idx, captured_at_ms, grid_w, grid_h)
            out_f.write(header)
            out_f.write(depth_resized.tobytes())
            written_frames += 1

            if idx % 100 == 0 or idx == len(det_timestamps) - 1:
                elapsed = time.time() - t0
                fps_proc = (idx + 1) / elapsed if elapsed > 0 else 0
                print(f"Frame {idx+1}/{len(det_timestamps)} ({fps_proc:.1f} FPS) - Depth min={depth_resized.min():.2f}m, max={depth_resized.max():.2f}m")

    cap.release()
    total_bytes = os.path.getsize(output_bin_path)
    print(f"Export complete: {written_frames} frames written to {output_bin_path} ({total_bytes / (1024*1024):.2f} MB)")

def main():
    # 1. Export 10942
    video_10942 = r"C:\Users\Admin\Downloads\10942.mp4"
    det_10942 = r"C:\Users\Admin\cloud-ai-os-visor-tracker\test-recordings\10942_production_yoloe\detections.txt"
    bin_10942 = r"C:\Users\Admin\cloud-ai-os-visor-tracker\test-recordings\10942_production_yoloe\depth_frames.bin"
    if os.path.isfile(video_10942) and os.path.isfile(det_10942):
        export_depth(video_10942, det_10942, bin_10942, crop_y_range=(438, 1878), grid_w=120, grid_h=160)
    else:
        print(f"Missing 10942 source: video={os.path.isfile(video_10942)}, det={os.path.isfile(det_10942)}")

    # 2. Export 10917
    video_10917 = r"C:\Users\Admin\cloud-ai-os-visor-tracker\test-recordings\10917\10917.mp4"
    det_10917 = r"C:\Users\Admin\cloud-ai-os-visor-tracker\test-recordings\10917\replay\detections.txt"
    bin_10917 = r"C:\Users\Admin\cloud-ai-os-visor-tracker\test-recordings\10917\replay\depth_frames.bin"
    if os.path.isfile(video_10917) and os.path.isfile(det_10917):
        # 10917 has 1080x2316 full screen
        export_depth(video_10917, det_10917, bin_10917, crop_y_range=None, grid_w=120, grid_h=256)
    else:
        print(f"Missing 10917 source: video={os.path.isfile(video_10917)}, det={os.path.isfile(det_10917)}")

if __name__ == '__main__':
    main()
