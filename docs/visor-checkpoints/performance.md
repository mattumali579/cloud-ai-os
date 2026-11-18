# Visor tracker performance, version 0.26.0

Package `com.mattumali.visortracker.stable`, versionCode 26. These numbers are not Galaxy S22 Ultra, Visor Live View, or glasses measurements. A 5-minute thermal soak was not run. That needs the phone. Phone battery and thermal throttling were not measured.

No neural-network fine-tuning was done. There is no labeled training set and no training environment. Model weights are unchanged.

## Before, emulator, version 0.25.0

From the people.jpg instrumentation run recorded in [pointing.md](pointing.md):

- 60,076 ms replay: 248 object inferences and 248 hand inferences, 4.13 fps each.
- Covering the locked person moved the lock to the other person in 4,062 ms.
- First call 2,017 ms (objects 303 ms, hand 1,142 ms).
- Hand score 0.931. Ray locked person #1. The center object was person #2.
- 49 invalid frames, all recovered.

The 4,062 ms was wall time of several sequential `detect()` calls. Each call ran the hand landmarker and EfficientDet. The lock then waited out 3 missing frames and 2 acquire frames. It was not a 4-second timer.

## After, unit tests, not a phone and not an emulator

`testDebugUnitTest`: 174 tests, 0 failures, 0 errors.

- A replacement that is already in the frame is held for one frame, then taken on the next. With a 33 ms fake clock that is 33 ms, which is under 500 ms. The test is `knownReplacementIsAcquiredOnTheNextPublishedFrame`.
- One empty frame, or one ray frame on a different object, does not switch. With no replacement visible, the lock still holds for 3 frames and then clears.
- While a lock is live, a free worker on 33 ms frames for 990 ms runs 4 object passes and 16 hand passes, 12 of them hands-only. That is the schedule math in `lockedTrackRunsHandsBetweenObjectPasses`. It assumes each pass finishes before the next camera frame. Emulator inference is slower than 66 ms, so this is not an emulator FPS.
- A 10 px textured shift moves the locked box from x=30 to x=40 on that same frame (`measuredShiftLandsInTheSameFrame`). The smoothing gain is 1, so the measured shift is not spread across later frames.
- A burst keeps only the newest frame. Three older frames are dropped. A fusion result with an older sequence is dropped and does not replace the published frame.
- Closing the session and attaching a new pipeline closes the previous one once. After reconnect, sequence 1 can publish again.

## Delegate

The object detector stays on CPU. A GPU object detect was not attempted. A GPU delegate that never returns is how the phone previously showed video and zero finished passes.

The hand landmarker tries GPU. On the emulator run for commit `c6f0937`, create did not keep the GPU landmarker (`handDelegate=CPU`, `attempted=GPU`, `fallbacks=1`). The CPU landmarker ran the real detects. Phone battery and thermal throttling were not measured, and this is not a phone GPU result.

## Tuner

`TrackerTuner` starts from the 0.25 replacement (4 observations) and the synthetic labeled cases. It accepted `knownReplacementFrames=2` because that score was higher. It rejected a hand score of 0.99, a 1-frame dwell, a 1-frame occlusion hold, ray hysteresis 1.01, and an object cadence of every 3rd locked pass. Those scores were not higher. Production uses the accepted replacement of 2 and the rejected knobs stay at the previous defaults: hand score 0.40, hysteresis 1.35, dwell 2, occlusion hold 3. The proving test is `tunerAcceptsTheFasterReplacementAndRejectsWorseKnobs`.

The correct track id in `files/failure-examples/failure-example.txt` is written only by `FailureLog.labelLatest` or the debug action `LABEL_FRAME`. The model's selected id is not copied into the correct id. Camera images are not written.

## After, emulator, people.jpg, x86_64, commit c6f0937

Instrumentation `OK (4 tests)` in 135.0 s. Artifact `visor-detector-simulation` id `14411907081` on run `44269359332`.

Pointing replay (`pointing-stage.txt`):

- Hand delegate CPU after the GPU attempt failed. Object delegate CPU.
- One hand, score 0.931. Ray target person #1. Center target person #2. Lock stayed on #1 after the 36 px shift.
- After the cover, the other person was acquired in 2 detect passes, 2,528 ms wall time. `knownCandidateFrames=2`.
- That 2,528 ms is two full emulator `detect()` calls. It is not the 33 ms unit-test interval, and it is not a phone measurement. The old 4,062 ms was about four of those calls.
- Overlay: 17,526 white pixels on the locked box, 3,506 cyan ray pixels.
- 60,014 ms combined replay: 387 object inferences and 387 hand inferences, 6.45 fps each. 387 frames contained an object. 77 invalid frames were injected and all 77 recovered.
- First call 1,878 ms (objects 1,008 ms, hand 711 ms). The Java heap reading at the end of the test was 2 MB. That figure does not include native MediaPipe memory.
- Objects-only 60,004 ms replay: 1,036 inferences, 1,036 painted frames, 207 invalid frames, all recovered.

Release APK artifact `visor-tracker-apk` id `14412196286`: `com.mattumali.visortracker.stable`, versionCode 26, versionName 0.26.0, signer SHA-256 `afb36d6baa0eb0b14e038ea7b141bc92662350bdfdf9be30151bb7d759a02308`. Publish stayed skipped. No release tag was created.
