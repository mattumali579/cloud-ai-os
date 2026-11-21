# VISOR — ONE CANONICAL END-TO-END COMPUTER VISION MISSION
**Status:** ACTIVE implementation contract, 2026-11-20. **Tracking issue:** [#40](https://github.com/mattumali579/cloud-ai-os/issues/40).
**Overrides:** previous Visor-only phase instructions, quick-release guidance, and replay-only tasks in #25, #36, #39, `.cursor/rules/visor-tracker-release.mdc`, and `docs/VISOR_TRACKER_RELEASE.md`. Historic README/changelog and tests are data, not current instructions. Does not override unrelated Jarvis computer control, creative or outreach policies.

## One outcome, not another prototype
On **Ray-Ban Meta camera → existing Visor Live View → Samsung S22 Ultra Android 16 → same stable Tracker overlay**, user points an index finger at an actual physical object: correct object highlights and automatically locks after stable pointing; switching pointed objects changes lock; dropping pointing releases. Other hand holding a phone, open palms and untracked hands **never** induce locks. It also recognizes and outlines objects, tracks them in motion, estimates relative scene depth, and optionally reads text and answers scene questions on user request. **No pinch, button or voice confirmation for the pointing interaction.** Do not claim holograms inside ordinary Ray-Ban Meta lenses: overlay is on the PHONE's Visor Live View, unless independent display hardware exists.

Do not spend another day adjusting `IndexPoint.kt` thresholds/2D line reversal. Implement and compare a **separate** spatial targeting engine using different evidence; keep old pipeline intact as baseline/fallback until the new one is proven. Do not create a second Android app, a server, Docker stack, an agent daemon, a dashboard, a new camera feed, or paid inference just to show results.

## Repo/workspace boundaries
- GitHub: `mattumali579/cloud-ai-os`; implementation branch: `feat/visor-computer-vision-e2e`; Android module: `experiments/visor-tracker-android/`.
- Reported Windows workspace: `C:\Users\Admin\cloud-ai-os-visor-tracker` (some previous work under `C:\Users\Admin\cloud-ai-os`). **Inspect `git status`, remote and worktree layout first**; never assume the local checkout is an exact GitHub clone. Do not reset/rebase/stash/overwrite dirty files without owner approval; preserve existing local code, build outputs and models.
- Current assessed baseline: v0.39.0 build 40, `fix/visor-depth-pointing-v31` at `a4502f4ef45bbcb4b4efde66edb16078ec0f3f37`. Newer local unpublished work might exist; inspect before changing anything.
- Existing dependencies: MediaPipe Tasks Vision 0.10.35, Visor Development Kit `com.macedonga.visor:kit:0.1.1`, EfficientDet Lite0/Lite2, BlazeFace, custom `MotionTracker`, `TrackIds`, `AimLock`, `VisionPipeline`, `OverlayTrackingService`, `TrackingOverlayView`, `RelativeDistanceEstimator` and tests.
- Existing VDK `camera.live` is the source of frame data. The Visor video should remain native; companion overlay is transparent and touch-through. Preserve app ID `com.mattumali.visortracker.stable`, existing signing certificate, in-place updater, and Tailscale split-tunnel compatibility.

## Required architecture (run in this order; no blind mass installation)

### Stage 0 — Observe and instrument (no release)
Record actual source/installed APK versions and model names, camera stream provenance, raw versus composited frames, detection vs render FPS, frame age/latency, temperature, memory and battery. On the existing `10932.mp4` (41.72-second screen recording), label at least the 0–15s backward phone lock, 18–25s phone-grip false point, 28–41s stale phone lock. This is **composited screen footage**, not unmodified camera frames. Add an opt-in private capture path **before drawing** the overlay, with timestamps, no automatic upload and user-controlled deletion. Keep all user footage out of this public repository.

### Stage 1 — Distinct vision engine, side-by-side
- Trial an exportable **YOLO26n-seg** Android model (objects/classes/masks) against existing EfficientDet Lite0/Lite2. Measure real per-class precision, masks, Android runtime, memory, thermals and licensing (Ultralytics AGPL/commercial terms). **Do not replace EfficientDet if YOLO is worse or slower.**
- Trial **Depth Anything V2 Small** mobile/INT8 depth or verified mobile **YOLO26n-depth** alternative; benchmark both if feasible, choose at most one. Validate transformation to original camera-frame coordinates and latency; relative depth is NOT meters. Metric distance may be shown only if calibrated and measured on known targets, with error bounds.
- Keep **MediaPipe Hand Landmarker** for 21 landmarks, normalized and world-landmark geometry where available, and handedness; handedness confidence is not individual landmark confidence. Hand-relative world landmarks are NOT absolute camera-to-hand pose.
- New `SpatialTargetingEngine` (exact file/class names flexible): maintain one *stable hand identity* and classify intentional pointing versus open palm, phone grip, curled finger, occlusion and unknown. Use hand pose and camera-space ray constraints plus scene-relative depth and object masks, where they improve evidence. **No backwards ray flip; no fingertip-inside-box=zero automatic hit; no nearest/center/highest-score fallback**. Depth alone cannot solve unknown 3D hand pose. Handle uncertain geometry as NO TARGET rather than fabricated precision.
- Use segmented object boundaries where YOLO delivers them; keep old motion tracking to propagate detections on in-between frames. Prevent object ID swaps on re-detection, camera movement, occlusion and label changes. Do not add ByteTrack/SAM 2/OpenCV as separate pipelines unless an A/B test shows a gap the existing tracking cannot solve. In particular, full SAM 2 video segmentation may overload the phone.
- New temporal state machine: `NO_HAND -> HAND_TRACKED -> POINTING -> HOVER -> LOCKED -> RELEASED`. A time-based stable hover automatically acquires; meaningful pointing elsewhere switches after hysteresis; hand no longer pointing releases after bounded, documented lost-frame grace. No ghost hand, grip-associated phone lock, impossible silent hand ownership swap or indefinite stale lock.
- Align timestamps for decoded frame, hands, detections, masks, depth estimates, camera orientation and overlay. Discard stale results; schedule inference adaptively. Motion/IMU access through Meta Wearables SDK can be explored **only if available and does not break existing Visor VDK streaming**; don't present an unverified IMU path as connected. Camera intrinsics, lens distortion, rotation, crop and hand-to-camera coordinates must be internally coherent; do not restart an endless standalone calibration/threshold-tuning phase.
- Run OCR with **Google ML Kit** and a vision-language system such as Gemini **on demand only**. Enforce explicit consent and authorization for cloud uploads/fees. Face *detection* may remain; identity recognition requires consent-based enrollment and local privacy controls. Neither OCR nor VLM may block the real-time pointing loop.
- No full SLAM until persistent world anchoring after looking away is actually required. On present hardware, single-camera depth/pose limits may prevent Quest-grade precision. Report such failure measured, not hidden.

### Stage 2 — Deterministic evidence, then device loop
Run existing Kotlin JVM/Android tests plus a **production pipeline** replay of actual raw frames with timestamped detector/hand/target outputs, annotated review clip and metrics. Python-only mocks or synthetic hand arrays are NOT success. Ground-truth labels must be human-assigned, separate from model predictions. Test adverse views (side-on, foreshortened, moving, occluded, overlapping near/far, multiple hands, grips, low light), and verify no auto lock on ambiguous frames. Compare same recordings before/after; log incorrect acquisitions, missed selections, switch/release time, dropouts, sustained FPS, median and p95 latency, battery and device thermal state.

Release candidate **proposed acceptance gates** (not claims of passing):
1. >=90% correct intended-object selection over >=30 labeled pointing trials, including off-center and different-depth targets.
2. ZERO unintended acquisitions during >=30 phone-grip/open-palm/no-point trials.
3. >=90% deliberate target switches over >=20 trials; lowering hand releases; no indefinite stale target.
4. Live View is actually under a correctly aligned touch-through overlay; correct app and certificate; target follows through motion and brief occlusion without jumping to unrelated objects.
5. A 15-minute authorized real-glasses S22 Ultra test with no crash, broken stream, or unacceptable heat/performance; stop and cool device if thermal safety requires. Report real phone telemetry rather than claim an arbitrary 30 FPS.
6. Optional OCR/Q&A exercised on glasses camera frames without blocking primary tracking.
7. Retest one full live recording after installing the candidate. A composited replay alone does not meet the device gate.

Report **SOURCE CODED / TESTS PASS / EMULATOR PASS / PHONE INSTALLED / PHYSICAL GLASSES PASS** separately with evidence links. No simulated status may be called hardware verified.

### Stage 3 — Package, verify and ship ONCE
Keep stable app ID, keystore and certificate. Build debug APK for tests but don't present it as an update to the signed installed app. Only after the gates pass, bump versionCode, build signed release, verify certificate SHA256 against installed stable app, install as an update (NEVER uninstall), verify on real glasses, then publish the release/tag and provide download link. **Unit tests alone are never authority to publish.** A GitHub Action is not physical-glasses evidence. Do not publish or retag an old APK merely to create progress.

## Agent execution loop / no abandonment
Antigravity: inspect current branch and dirty Windows worktree; choose first failing end-to-end layer; implement smallest coherent feature of new architecture; run tests; observe failures; correct; repeat; commit meaningful milestones to this branch and update #40 with metrics/evidence/blocker. Continue across the defined phases rather than ending after a proposal, an implementation claim, unit tests or an APK build. If two fixes to the same layer fail, re-evaluate the architecture, don't tune another threshold. No arbitrary new model/stack; evaluate measurable alternatives first.

**Cannot do in agent without hardware:** a remote agent cannot physically wear glasses, authorize Android prompts, confirm visible Live View, or measure heat from a user's device without an attached authorized device/test harness. When blocked, request ONLY the precise missing user/device action and provide exact instructions to resume; do not claim that GitHub issues, rules or commits themselves start a local Antigravity session or guarantee uninterrupted background execution. Never promise completion without evidence.

## Live results ledger template (keep in #40; update after every real milestone)
| Stage | Source/code proof | Replay/test proof | Android device proof | Blocker |
|---|---|---|---|---|
| Baseline | PENDING | PENDING | PENDING | Inspect exact current local build |
| YOLO/masks | PENDING | PENDING | PENDING | Model benchmark |
| Depth | PENDING | PENDING | PENDING | Relative vs metric validation |
| New spatial targeting | PENDING | PENDING | PENDING | Accurate hand→scene pose |
| Tracking/lock | PENDING | PENDING | PENDING | Negative test failures |
| OCR/vision QA | PENDING | PENDING | PENDING | Consent/runtime |
| Signed install & real glasses | PENDING | PENDING | PENDING | Physical authorized test |

## Official upstream references
- [Ultralytics YOLO26 tasks](https://docs.ultralytics.com/models/yolo26)
- [Ultralytics depth model](https://docs.ultralytics.com/tasks/depth)
- [MediaPipe Hand Landmarker Android](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/android)
- [Meta Wearables SDK](https://developers.meta.com/wearables/device-access-toolkit/)
- [Antigravity workspace rules](https://antigravity.google/docs/rules/)

**No other Visor tracking rule is authoritative.**
