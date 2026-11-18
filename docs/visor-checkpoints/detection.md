# Detection checkpoint

Camera-frame ingestion, YUV conversion, detector scheduling, presets, and label policy. Not a hardware or glasses verification. The rest of Visor Tracker is not claimed complete.

## Bugs found

1. **Plane reuse called `ArrayList.addLast`.** `PlanePool.give` compiled to Java 21 `ArrayList.addLast`. This app does not desugar that method, and Android's `ArrayList` does not provide it. The first `releasePlanes()` after a detection throws `NoSuchMethodError`. That is an `Error`, so `OverlayTrackingService`'s `catch (Exception)` does not record it, and the throw happens in `finally` before `processing` is cleared. Later frames never start a detector pass, and the overlay does not show a stage fault.

2. **Broken YUV was painted black and treated as a successful frame.** `writeArgb` substituted limited-range black (Y=16) or neutral chroma when a sample index was outside the plane, including when the index overflowed a 32-bit stride multiply. `outputSize(0, …)` became a 1×`Int.MAX_VALUE` image. An empty plane or a zero stride produced a flat image. `toBitmap` then returned, the service cleared the `yuv` fault, and the detector reported zero objects.

3. **A snapshot could be converted after its buffers were returned to the pool.** `toBitmap` did not look at `planesReleased`, so a second conversion could read a buffer already handed to the next frame.

4. **The box score could come from a category that was not drawn.** `VisionPipeline` used `maxOf` on every raw score. A blank name or a NaN could outrank the finite category `LabelPolicy` actually kept. Scores under `LabelPolicy.MIN_SCORE` (0.25) are still omitted. A Fast-mode score of 0.25 is kept and shown as `object` when it is below the ordinary-name floor. That part already matched the tests and was not the silent drop.

5. **A hand or face exception discarded object boxes.** Both ran inside the same `try` as EfficientDet. `runOptional` now catches those failures, stores `VisionPipeline.lastOptionalFault`, and still returns the object boxes. Hands and faces stay off unless the checkbox is on.

`DetectorSchedule` already starts when inference is due and the worker is free, and it does not take grayscale success as an input. `StageFaults` already keeps a detector fault when another stage is cleared. Those two files were not changed.

## Files changed

- `experiments/visor-tracker-android/app/src/main/java/com/mattumali/visortracker/YuvSnapshot.kt`
- `experiments/visor-tracker-android/app/src/main/java/com/mattumali/visortracker/VisionPipeline.kt`
- `experiments/visor-tracker-android/app/src/main/java/com/mattumali/visortracker/LabelPolicy.kt`
- `experiments/visor-tracker-android/app/src/test/java/com/mattumali/visortracker/YuvSnapshotTest.kt`
- `experiments/visor-tracker-android/app/src/test/java/com/mattumali/visortracker/LabelPolicyTest.kt`
- `experiments/visor-tracker-android/app/src/test/java/com/mattumali/visortracker/StageFaultsTest.kt`
- `experiments/visor-tracker-android/app/src/test/java/com/mattumali/visortracker/DetectionPresetTest.kt`

Not modified: `DetectorSchedule.kt`, `StageFaults.kt`.

## Tests added

- `YuvSnapshotTest.paddedCropReadsThePixelInsideTheCrop`
- `YuvSnapshotTest.semiPlanarChromaKeepsLimitedRangeRed`
- `YuvSnapshotTest.emptyOrZeroSizedFramesFailInsteadOfBecomingBlack`
- `YuvSnapshotTest.aShortLumaPlaneOrOverflowingStrideIsNotPaintedBlack`
- `YuvSnapshotTest.releasedPlanesCannotBeConverted`
- `YuvSnapshotTest.planePoolDoesNotCallJdk21AddLast`
- `LabelPolicyTest.aScoreTheFastDetectorReturnsIsNotDropped`
- `LabelPolicyTest.aBlankOrNanScoreCannotReplaceTheRealCategoryScore`
- `StageFaultsTest.anOptionalModelFailureDoesNotDiscardObjectBoxes`
- `DetectionPresetTest` now also asserts `FAST.scoreThreshold <= LabelPolicy.MIN_SCORE`

The new YUV tests throw on the old silent-black and empty-frame behavior. The pool test reads `PlanePool.class` and fails while the bytecode still calls `addLast`.

## Command

`experiments/visor-tracker-android` has no `gradlew.bat`. The run used the Gradle 9.6.0 binary already on this machine (`C:\Users\Admin\AppData\Local\Temp\gradle-9.6.0\bin\gradle.bat`), which matches `experiments/visor-tracker-android/.gradle/9.6.0`. `ANDROID_HOME` was set to `C:\Users\Admin\AppData\Local\Android\Sdk` for that process because `local.properties` is absent. `local.properties` was not written.

The Gradle lock `docs/visor-checkpoints/GRADLE_LOCK` was created with .NET `File.Open` `CreateNew` and exclusive share, then deleted after the process exited. `app/build` and signed APKs were not deleted.

```
gradle :app:testDebugUnitTest --tests com.mattumali.visortracker.DetectorScheduleTest --tests com.mattumali.visortracker.LabelEvaluationTest --tests com.mattumali.visortracker.DetectionPresetTest --tests com.mattumali.visortracker.LabelPolicyTest --tests com.mattumali.visortracker.YuvSnapshotTest --tests com.mattumali.visortracker.StageFaultsTest --tests com.mattumali.visortracker.PipelineBenchmarkTest
```

## Pass/fail

Pass. 30 tests, 0 failures, 0 errors, 0 skipped.

| Class | Tests |
| --- | ---: |
| DetectorScheduleTest | 1 |
| LabelEvaluationTest | 3 |
| DetectionPresetTest | 3 |
| LabelPolicyTest | 9 |
| YuvSnapshotTest | 10 |
| StageFaultsTest | 3 |
| PipelineBenchmarkTest | 1 |

## Leftover risks

- No phone, Visor, or glasses run. Unit tests do not execute MediaPipe.
- `OverlayDiagnostics.diagnose` treats a failure as a detector failure only when the text starts with `MediaPipe`. Stage text is `detect: …`, so a recorded detector fault is shown as `STREAM FAILED`. Suggested change, not applied: in `OverlayDiagnostics.kt`, treat a failure as `DETECTOR_FAILED` when it starts with `MediaPipe`, starts with `detect:`, or contains `Decode/detection`.
- `VisionPipeline.lastOptionalFault` is not copied into `StageFaults`. A hand or face failure is logged and kept on the pipeline, and object boxes are still returned, but the notification will not show it until `OverlayTrackingService` records that string. Suggested change, not applied: after a successful `detect`, if `pipeline.lastOptionalFault` is non-blank, call `stageFail("hands", pipeline.lastOptionalFault)`.
- `OverlayTrackingService` still clears `processing` after `releasePlanes()`. Any future `Error` from that `finally` can wedge the worker again. Suggested change, not applied: clear `detectorBusy` and `processing` in an inner `finally` that runs even when `releasePlanes()` throws.
- Fast mode still uses score floor 0.25, and names from 0.25 up to the ordinary 0.45 floor are drawn as `object` rather than dropped. Accurate mode remains 0.45 because `DetectionPresetTest` requires that. MediaPipe will not return Accurate-mode boxes below 0.45.
- `YuvSnapshot.toBitmap` still reuses one bitmap on the detector worker. `MPImage.close()` recycles that bitmap, so `detect` does not close the image. Closing it would blank the next frame.
