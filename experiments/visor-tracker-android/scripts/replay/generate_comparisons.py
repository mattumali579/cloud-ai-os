import cv2
import json
import os
import glob
import numpy as np

# This script generates side-by-side comparison images between:
# 1) Ground truth reference / raw keyframe with reference candidate
# 2) Production model output overlay from 10942_overlay_replay_after_864d4e8.mp4

video_path = r"C:\Users\Admin\cloud-ai-os-visor-tracker\test-recordings\10942\10942_overlay_replay_after_864d4e8.mp4"
decisions_path = r"C:\Users\Admin\cloud-ai-os-visor-tracker\test-recordings\10942\decisions.jsonl"
out_dir = r"C:\Users\Admin\cloud-ai-os-visor-tracker\test-recordings\10942\comparisons"
os.makedirs(out_dir, exist_ok=True)

# 16 Ground truth reference timestamps and targets from VISOR_10942_VISUAL_REFERENCE.md
REFERENCE_TARGETS = [
    (0.8, "Laundry basket", "frame_0024"),
    (3.0, "Open closet / clothing", "frame_0091"),
    (5.0, "Portable air conditioner", "frame_0151"),
    (7.0, "Portable air conditioner", "frame_0212"),
    (9.5, "Window sill with containers", "frame_0288"),
    (12.0, "Conical hat", "frame_0363"),
    (15.5, "Laptop", "frame_0469"),
    (18.0, "Paper roll on table", "frame_0545"),
    (21.0, "NO TARGET (curled hand)", "frame_0636"),
    (24.0, "White plastic-covered item", "frame_0727"),
    (26.5, "Whiteboard", "frame_0802"),
    (28.0, "Black backpack on chair", "frame_0848"),
    (30.5, "Gray garment on chair", "frame_0924"),
    (33.0, "Chair lower frame", "frame_0999"),
    (36.0, "Books on shelf", "frame_1090"),
    (38.5, "NO CERTAIN TARGET", "frame_1166"),
]

def main():
    if not os.path.exists(video_path):
        print(f"Video {video_path} not found yet.")
        return
        
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"Loaded overlay replay: {total_frames} frames @ {fps:.2f} FPS")

    # Load decisions
    decisions = []
    if os.path.exists(decisions_path):
        with open(decisions_path, "r") as f:
            for line in f:
                decisions.append(json.loads(line))

    # Reference image 01 (Laundry basket)
    ref_laundry = None
    ref_laundry_path = r"C:\Users\Admin\Downloads\ChatGPT Image Nov 21, 2026, 01_24_07 AM.jpg"
    if os.path.exists(ref_laundry_path):
        ref_laundry = cv2.imread(ref_laundry_path)

    for idx, (t, target_desc, ftag) in enumerate(REFERENCE_TARGETS):
        fidx = int(round(t * fps))
        if fidx >= total_frames:
            fidx = total_frames - 1
            
        cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
        ret, overlay_frame = cap.read()
        if not ret:
            continue
            
        # Decision at this frame
        dec = decisions[fidx] if fidx < len(decisions) else {}
        state = dec.get("state", "NONE")
        lock_id = dec.get("lock_id")
        hit_id = dec.get("hit_id")
        
        lock_label = "NO_TARGET"
        if lock_id is not None:
            for b in dec.get("boxes", []):
                if b.get("track_id") == lock_id:
                    lock_label = f"LOCKED #{lock_id} {b.get('label', 'object').upper()}"
        elif hit_id is not None:
            for b in dec.get("boxes", []):
                if b.get("track_id") == hit_id:
                    lock_label = f"HOVER #{hit_id} {b.get('label', 'object').upper()}"
        elif state == "POINTING_CONFIRMED" or state == "POINTING":
            lock_label = "POINTING (NO TARGET HIT)"

        # Side-by-side comparison card
        # Left side: Reference description or screenshot
        # Right side: Overlay frame
        target_h = 1080
        target_w = int(overlay_frame.shape[1] * (target_h / overlay_frame.shape[0]))
        right_panel = cv2.resize(overlay_frame, (target_w, target_h))

        left_panel = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        left_panel[:] = (30, 30, 35)

        if idx == 0 and ref_laundry is not None:
            # Show actual ChatGPT reference image on left
            rh, rw, _ = ref_laundry.shape
            scale = min(target_w / rw, target_h / rh)
            new_w = int(rw * scale)
            new_h = int(rh * scale)
            resized_ref = cv2.resize(ref_laundry, (new_w, new_h))
            y_off = (target_h - new_h) // 2
            x_off = (target_w - new_w) // 2
            left_panel[y_off:y_off+new_h, x_off:x_off+new_w] = resized_ref
            # Banner
            cv2.rectangle(left_panel, (0, 0), (target_w, 50), (20, 20, 20), -1)
            cv2.putText(left_panel, f"CHATGPT REFERENCE IMAGE ({target_desc})", (15, 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 128), 2, cv2.LINE_AA)
        else:
            # Draw reference card
            cv2.rectangle(left_panel, (0, 0), (target_w, 60), (45, 45, 55), -1)
            cv2.putText(left_panel, f"KEYFRAME {idx+1:02d} | t={t:04.1f}s", (20, 42),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 220, 255), 2, cv2.LINE_AA)
            
            cv2.putText(left_panel, "PROVISIONAL TARGET:", (25, 120),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 180, 180), 1, cv2.LINE_AA)
            cv2.putText(left_panel, target_desc, (25, 160),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2, cv2.LINE_AA)
            
            # Load raw camera crop if available
            raw_crop_path = rf"C:\Users\Admin\cloud-ai-os-visor-tracker\test-recordings\10942\extracted_key_frames\{ftag}_*.jpg"
            raw_matches = glob.glob(raw_crop_path)
            if raw_matches:
                raw_img = cv2.imread(raw_matches[0])
                if raw_img is not None:
                    # Show viewport crop
                    crop = raw_img[438:1878, :]
                    ch, cw, _ = crop.shape
                    scale = min((target_w - 40) / cw, (target_h - 220) / ch)
                    nw, nh = int(cw * scale), int(ch * scale)
                    rcrop = cv2.resize(crop, (nw, nh))
                    yo = 200 + (target_h - 220 - nh) // 2
                    xo = 20 + (target_w - 40 - nw) // 2
                    left_panel[yo:yo+nh, xo:xo+nw] = rcrop
                    cv2.rectangle(left_panel, (xo, yo), (xo+nw, yo+nh), (100, 100, 100), 1)

        # Header bar on right panel
        cv2.rectangle(right_panel, (0, 0), (target_w, 50), (20, 20, 20), -1)
        color = (0, 255, 60) if "LOCKED" in lock_label else ((0, 220, 255) if "HOVER" in lock_label else (200, 200, 200))
        cv2.putText(right_panel, f"PRODUCTION MODEL: {lock_label}", (15, 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.70, color, 2, cv2.LINE_AA)

        # Combine side by side
        sbs = np.hstack([left_panel, right_panel])
        out_fn = os.path.join(out_dir, f"compare_{idx+1:02d}_t{t:04.1f}s_{target_desc.split()[0].lower()}.jpg")
        cv2.imwrite(out_fn, sbs)
        print(f"Saved {out_fn}")

    cap.release()
    print(f"All {len(REFERENCE_TARGETS)} comparison images generated in {out_dir}")

if __name__ == "__main__":
    main()
