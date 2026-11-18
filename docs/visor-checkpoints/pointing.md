# Finger pointing and target reacquisition

Version 0.25.0, versionCode 25, is the measurement below. Version 0.26.0 keeps this ray and records the speed changes in [performance.md](performance.md). Package remains `com.mattumali.visortracker.stable`.

## What changed

- MediaPipe Hand Landmarker runs on the CPU delegate, on a frame whose long side is at most 640px. Landmarks are mapped into the EfficientDet frame. Both models run. A hand failure does not drop object boxes.
- An extended index finger (wrist, MCP, PIP, DIP, tip, other fingers curled, handedness at least 0.40) builds a 2D ray from the fingertip. The object the ray enters is the target. The current lock stays when it is also on that ray.
- Two steady frames lock it. Dropping the hand does not move the lock back to the screen center. One stray ray frame does not switch it.
- If the locked track is missing and no other object is visible, the lock is held for 3 frames. Version 0.26.0 does not add that hold when a replacement is already in the frame. See [performance.md](performance.md).
- The locked box is white. The ray and the five index joints are cyan.
- With no 21-point hand, the center of the picture still locks.

## Limitation

The ray is 2D on the camera image. It is not a measured 3D direction. Two objects on the same ray cannot be separated by depth.

## Proof

Isolated geometry is `IndexPointTest` and `AimLockTest`. Those use constructed landmarks.

Real recognition is the emulator test `realHandPointsAtOneObjectThenTheOtherIsAcquired` on `people.jpg`. Commit `03ad6452e6d34827441870e8e541d62b4935a8e0` passed `OK (4 tests)` in 136.4s. JVM tests do not load the native models.

Measured on that run, from `pointing-stage.txt`:

- One hand, score 0.931. Wrist (298, 137), index MCP (321, 126), PIP (331, 125), DIP (338, 125), tip (344, 125).
- Four EfficientDet objects. The ray selected person #1. The object nearest the center was person #2.
- Lock stayed on #1 after a 36px shift. After #1 was covered, lock moved to person #2 in 4062ms.
- Overlay painted 17,526 white pixels on the locked box and 3,506 cyan ray pixels. The saved frame shows the white box, the label `person #1`, and the cyan finger joints and ray.
- 60,076ms continuous replay: 248 object inferences and 248 hand inferences, 4.13 fps each, 248 frames still contained an object. 49 invalid frames were injected and all 49 recovered.
- First call took 2017ms (objects 303ms, hand 1142ms). The two-frame dwell then reused that finished frame.

The release APK is `com.mattumali.visortracker.stable` versionCode 25, signer SHA-256 `afb36d6baa0eb0b14e038ea7b141bc92662350bdfdf9be30151bb7d759a02308`.

A tall phone cover-crops a wide camera frame, so a fingertip at the extreme edge can fall outside the preview even though the ray is correct in camera coordinates. The proof frame uses the detection frame's 16:9 shape so the ray stays on screen.
