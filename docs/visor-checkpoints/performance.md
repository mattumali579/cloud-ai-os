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

The object detector stays on CPU. A GPU object detect is not attempted here, because a GPU delegate that never returns is how the phone previously showed video and zero finished passes.

The hand landmarker tries GPU. If create does not return within 3 seconds, or the first detect does not return within 2.5 seconds, the pipeline keeps the CPU landmarker and continues. The winner is `handDelegate` in `pointing-stage.txt` after the emulator run. This machine did not run the emulator, so the winner is not claimed here.

## Tuner

`TrackerTuner` starts from the 0.25 replacement (4 observations) and the synthetic labeled cases. It accepted `knownReplacementFrames=2` because that score was higher. It rejected a hand score of 0.99, a 1-frame dwell, a 1-frame occlusion hold, ray hysteresis 1.01, and an object cadence of every 3rd locked pass. Those scores were not higher. Production uses the accepted replacement of 2 and the rejected knobs stay at the previous defaults: hand score 0.40, hysteresis 1.35, dwell 2, occlusion hold 3.

The correct track id in `files/failure-examples/failure-example.txt` is written only by `FailureLog.labelLatest` or the debug action `LABEL_FRAME`. The model's selected id is not copied into the correct id. Camera images are not written.

## Emulator after

The people.jpg instrumentation test still runs both models on each `detect()` call and records `handDelegate`, `switchToBMs`, `switchDetectPasses`, `knownCandidateFrames`, fps, invalid frames, and heap. Those lines are the emulator after numbers. They are filled from the CI artifact for this commit, not from a phone.
