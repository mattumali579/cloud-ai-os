# Visor tracker performance, version 0.27.0

Package `com.mattumali.visortracker.stable`, versionCode 27. These numbers are not Galaxy S22 Ultra, Visor Live View, or glasses measurements. A 5-minute thermal soak was not run. `adb devices` was empty on the machine that built this commit, so no phone was measured. Phone battery and thermal throttling were not measured.

No neural-network fine-tuning was done. There is no labeled training set and no training environment. Model weights are unchanged.

The finger ray is 2D on the camera image. It cannot measure depth. The Three.js page at `experiments/visor-tracker-sim/` uses an idealized 3D raycast. That page and the Kotlin tracker do not share an executable.

## Before, emulator, version 0.26.0, people.jpg

From the 0.26.0 instrumentation run, not a phone:

- 60,014 ms replay: 387 object inferences and 387 hand inferences, 6.45 fps each.
- Covering the locked person moved the lock to the other person in 2,528 ms, two full `detect()` passes.
- Hand delegate CPU after a GPU attempt (`attempted=GPU`, `fallbacks=1`). Object delegate CPU.
- Ray locked person #1. The center object was person #2. The lock held through a 36 px shift.
- Overlay: 17,526 white pixels on the locked box, 3,506 cyan ray pixels.
- 77 invalid frames, all recovered.

The 6.45 fps figure is combined `detect()` wall time on the emulator. It is not camera FPS.

## After, unit tests, not a phone and not an emulator

`testDebugUnitTest`: 185 tests, 0 failures, 0 errors. The simulator policy test is 5 tests, 0 failures (`node --test policy.test.js`).

- `knownReplacementConfirmsOnTheSameDetection`: the replacement is already in the frame, and `observeKnownReplacement` takes it inside one call. The call was under 50 ms on the JVM. That is two AimLock observations and zero detector calls.
- `knownReplacementIsAcquiredOnTheNextPublishedFrame` is unchanged: two published frames on a 33 ms fake clock, 33 ms, under 500 ms.
- `oneObservationStillWaitsWhenTheCallerDoesNotReuseTheFrame`: one `observe` still does not switch.
- `knownReplacementDoesNotSkipTheEmptyOcclusionHold`: with no replacement, the lock still holds for 3 frames.
- `knownReplacementSwitchesOnTheDetectionThatShowsIt`: the distance estimator uses the fast path only when the other object was already in the previous frame. `anUnseenReplacementStillWaitsAFrame` still waits.
- `GestureGateTest`: an extended index is pointing. A missing hand does not release a finger lock. One closed frame does not switch or release. Four closed frames release the lock. `sentExternalCommand` stays false. There is no Jarvis or desktop call.
- `tunerAcceptsTheFasterReplacementAndRejectsWorseKnobs`: accepted `knownReplacementFrames=2`. Rejected hand score 0.99, dwell 1, occlusion hold 1, hysteresis 1.01, object-every-3rd, `releaseFrames=1`, and `releaseFrames=8`. The kept release count is 4. A worse score is not stored.
- A 10 px textured shift still moves the locked box on that same frame (`measuredShiftLandsInTheSameFrame`). Smoothing gain stays 1.
- While a lock is live, the schedule math is unchanged: hands can run between object passes on one worker. MediaPipe IMAGE mode is still not called on one detector from two threads.

## What was not changed to chase FPS

- One inference worker. A second detector instance was not added. There is no measurement here that a second worker is faster without a late result replacing a newer frame.
- Running mode stays IMAGE. LIVE_STREAM and VIDEO were not enabled. A SIGSEGV in those modes would kill the process, and this tree has no successful repeated-detect probe for them.
- `numHands` stays 2. The lock proof is not run with one hand.
- Hand long side stays 640. The emulator test records one extra 320 px detect in `pointing-stage.txt` and does not use that size for the lock.
- Object model stays EfficientDet Lite0, 384 px, score 0.25.
- CPU fallback for the hand landmarker stays. The object detector stays on CPU.

## After, emulator, people.jpg, x86_64

The detector-simulation job for this commit is the measurement. It is not a phone result. The lines below are filled from that job's `pointing-stage.txt`. Until that file is read, the 0.26.0 numbers above are the last emulator evidence.

Emulator after: pending the Actions artifact for this commit.
