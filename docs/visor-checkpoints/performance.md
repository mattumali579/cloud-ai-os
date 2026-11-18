# Visor tracker performance, version 0.27.0

Package `com.mattumali.visortracker.stable`, versionCode 27. These numbers are not Galaxy S22 Ultra, Visor Live View, or glasses measurements. A 5-minute thermal soak was not run. `adb devices` was empty, so no phone was measured. Phone battery and thermal throttling were not measured.

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

## After, emulator, people.jpg, x86_64, commit 414330f

Instrumentation `OK (4 tests)`. Artifact `visor-detector-simulation` id `14415856391` on run `44277058072`. This is not a phone, not Visor Live View, and not the glasses.

Pointing replay (`pointing-stage.txt`):

- Hand delegate CPU after the GPU attempt failed (`attempted=GPU`, `fallbacks=1`). Object delegate CPU. Running mode IMAGE. `numHands=2`. Hand long side 640.
- One hand, score 0.931. Ray target person #1. Center target person #2. Lock stayed on #1 after the 36 px shift. `pointToLockMs=1`.
- After the cover, person #2 was already known. `reusedKnownFrame=true`. One detect pass, `switchFirstDetectMs=984` (objects 866 ms, hand 106 ms). `switchToBMs=1016`. `knownCandidateFrames=1`.
- That 1,016 ms is one emulator `detect()` plus the second AimLock observation on the same boxes. It is not under 500 ms. The object model on that pass took 866 ms, so a second skipped detect cannot land under 500 ms on this image. The 0.26.0 figure was 2,528 ms for two full passes. The logical switch in the unit test remains one detection, two observations.
- A separate 320 px hand detect in the same job still found the ray (`hand320Ray=true`, `hand320Ms=508`). Production stayed at 640. That 508 ms is one extra detect, not the 60 s loop.
- Overlay: 17,526 white pixels on the locked box, 3,506 cyan ray pixels.
- 60,030 ms replay: 373 object inferences and 373 hand inferences. Wall-clock rate 6.21 fps each, because the loop also stepped motion and drew. Detect-only rate 6.41 fps (`detectMs=58235`). Before, the 6.45 fps loop did not draw or step motion. Camera FPS was not measured. Draw FPS 6.21 and motion FPS 6.21 are not inferences (`motionMoved=46`). 373 frames contained an object. 74 invalid frames were injected and all 74 recovered.
- First call 3,469 ms (objects 1,803 ms, hand 1,051 ms). The Java heap reading at the end of the test was 2 MB. That figure does not include native MediaPipe memory.

Objects-only 60,001 ms replay (`replay-stage.txt`): 960 inferences, 960 painted frames, 192 invalid frames, all recovered.

## What was not changed to chase FPS

- One inference worker. A second detector instance was not added. There is no measurement that a second worker is faster without a late result replacing a newer frame.
- Running mode stays IMAGE. LIVE_STREAM and VIDEO were not enabled. A SIGSEGV in those modes would kill the process, and this tree has no successful repeated-detect probe for them.
- `numHands` stays 2. The people.jpg proof found one hand at score 0.931 with that setting. It was not reduced.
- Hand long side stays 640. The 320 px probe above still saw the ray, and the lock proof was not moved onto 320.
- Object model stays EfficientDet Lite0, 384 px, score 0.25.
- CPU fallback for the hand landmarker stays. The object detector stays on CPU.

Release APK artifact `visor-tracker-apk` id `14415995260` on run `44277058072`: `com.mattumali.visortracker.stable`, versionCode 27, versionName 0.27.0, signer SHA-256 `afb36d6baa0eb0b14e038ea7b141bc92662350bdfdf9be30151bb7d759a02308`. Publish stayed skipped. No release tag was created.

The same commit also completed run `44277049465` with conclusion success. The stage numbers above are from run `44277058072`.

The same tracker code was measured again on commit `7e627cf`, run `44279416931`, artifact `visor-detector-simulation` id `14417340466`. `OK (4 tests)`. Still emulator people.jpg, not a phone.

- Switch stayed one detect pass: `reusedKnownFrame=true`, `switchToBMs=741`, objects 324 ms, hand 415 ms. Still above 500 ms.
- 60,077 ms replay: 210 object and 210 hand inferences. Wall-clock 3.50 fps. Detect-only 3.67 fps (`detectMs=57293`). Draw and motion were 3.50 fps and are not inferences.
- Hand score 0.931, ray person #1, center person #2, GPU hand fell back to CPU, overlay 17,526 white and 3,506 cyan.
- The 320 px probe still found the ray (`hand320Ms=818`). Production stayed at 640.

The two emulator samples of the same code are 6.41 and 3.67 detect-only fps, and 1,016 ms and 741 ms to switch. Both are one detect instead of the 0.26.0 two-detect 2,528 ms. Neither switch is under 500 ms. The spread is emulator variance, not a phone result.
