# Finger pointing and target reacquisition

Version 0.25.0, versionCode 25. Package remains `com.mattumali.visortracker.stable`.

## What changed

- MediaPipe Hand Landmarker runs on the CPU delegate, on a frame whose long side is at most 640px. Landmarks are mapped into the EfficientDet frame. Both models run. A hand failure does not drop object boxes.
- An extended index finger (wrist, MCP, PIP, DIP, tip, other fingers curled, handedness at least 0.40) builds a 2D ray from the fingertip. The object the ray enters is the target. The current lock stays when it is also on that ray.
- Two steady frames lock it. Dropping the hand does not move the lock back to the screen center. One stray ray frame does not switch it.
- If the locked track is missing for 3 frames, the next visible object is taken by highest score, then largest box, then lowest track id. That also takes two frames.
- The locked box is white. The ray and the five index joints are cyan.
- With no 21-point hand, the center of the picture still locks.

## Limitation

The ray is 2D on the camera image. It is not a measured 3D direction. Two objects on the same ray cannot be separated by depth.

## Proof

Isolated geometry is `IndexPointTest` and `AimLockTest`. Those use constructed landmarks.

Real recognition is the emulator test `realHandPointsAtOneObjectThenTheOtherIsAcquired` on `people.jpg`: a photograph of an extended index finger aimed across two people. It runs Hand Landmarker and EfficientDet-Lite0, the production ray and `AimLock`, then paints `TrackingOverlayView`. The stage file is `pointing-stage.txt` and the rendered frame is `pointing-overlay.png` in the `visor-detector-simulation` artifact. JVM tests do not load the native models.
