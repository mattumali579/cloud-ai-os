# Visor Tracker integration

Integration of the detection, lock, overlay, detector-fault label, and same-frame lock slices. This is not a phone, Visor, or glasses verification.

Starting point before the local edits was `bcad12477e9a522b13f74090781a250a5f7aeaa6`. Those edits were uncommitted and were kept.

## What this pass changed

- After a successful object `detect`, `OverlayTrackingService` copies `VisionPipeline.lastOptionalFault` into `StageFaults`. The notification extra includes a `hands:` or `faces:` line while that fault is set, including when the hop is still `TRACKING`. A later clean optional scan clears it. With nothing drawn, `hands:` and `faces:` are `DETECTOR FAILED`, not `STREAM FAILED`.
- `MainThreadLockWait.finish` does not treat an interrupted or rejected main-thread wait as the previous lock. `queueDetection` is false, so that detection is not pumped with the old id. The same-frame order is unchanged: apply the lock, then read the id, then queue the draw. An interrupt after the update has stored the new id still publishes that id.
- Not changed: white lock stroke, center-crop cover math, the 0.45 accurate-mode floor, and "picture center is not a hand landmark".

## Commands

Working directory: `experiments/visor-tracker-android`. There is no `gradlew.bat`. CI uses Gradle 9.6.0 (`gradle --no-daemon`). This machine used the same binary. `ANDROID_HOME` was empty in the shell and was set for the process to `C:\Users\Admin\AppData\Local\Android\Sdk`. `local.properties` was not written. `VISOR_SIGNING_STORE_FILE` was unset. No emulator was started.

```
$env:ANDROID_HOME = "C:\Users\Admin\AppData\Local\Android\Sdk"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
& "C:\Users\Admin\AppData\Local\Temp\gradle-9.6.0\bin\gradle.bat" --no-daemon :app:testDebugUnitTest
& "C:\Users\Admin\AppData\Local\Temp\gradle-9.6.0\bin\gradle.bat" --no-daemon assembleDebug assembleRelease
```

## Pass/fail

Unit suite: **pass**. `BUILD SUCCESSFUL`, exit 0. 144 tests, 0 failures, 0 errors, 0 skipped.

| Class | Tests |
| --- | ---: |
| AimLockTest | 6 |
| BoxHoldTest | 3 |
| CoverGeometryTest | 5 |
| DetectionPresetTest | 3 |
| DetectorScheduleTest | 1 |
| FrameBudgetTest | 3 |
| LabelEvaluationTest | 3 |
| LabelPolicyTest | 9 |
| LockPerformanceTest | 14 |
| MotionTrackerTest | 10 |
| NearbyCandidatesTest | 10 |
| OptionalModelFaultTest | 3 |
| OverlayDiagnosticsTest | 27 |
| OverlayFrameSelectionTest | 5 |
| PipelineBenchmarkTest | 1 |
| PipelineStatsTest | 4 |
| RelativeDistanceEstimatorTest | 15 |
| StageFaultsTest | 3 |
| StageTimingTest | 1 |
| TrackIdsTest | 8 |
| YuvSnapshotTest | 10 |

`assembleDebug` and `assembleRelease`: **pass**. `BUILD SUCCESSFUL`, exit 0.

## APK paths

Not installed on a phone.

- Debug: `experiments/visor-tracker-android/app/build/outputs/apk/debug/app-debug.apk` (53,623,322 bytes, 2026-11-17 22:32 local). Local debug signing. This is not the stable phone certificate.
- Release without the stable keystore: `experiments/visor-tracker-android/app/build/outputs/apk/release/app-release-unsigned.apk` (35,070,513 bytes, 2026-11-17 22:32 local). CI uploads `app-release.apk` only after `VISOR_SIGNING_*` signs it. Those secrets are not on this machine, so the signed CI filename was not produced here.

## Leftover risks

- No Galaxy S22 Ultra, Visor Live View, or Ray-Ban Meta glasses run. The required hardware check is still: glasses camera → Visor Live View on the phone → this overlay draws the locked object → a spoken or on-device result confirms the box matches the object in view.
- A motion step that already read the previous lock id can still post one old box. The detection whose wait was interrupted is not queued with that id. The unit test calls `MainThreadLockWait.finish` directly. It does not construct the service or a real `Looper`.
- `processing` is still cleared after `releasePlanes()`. An `Error` from that `finally` can wedge the detector worker. Not changed in this pass.
- `YuvSnapshot.toBitmap` still reuses one bitmap on the detector worker. `detect` does not close the `MPImage`, because closing it recycles that bitmap.
- Fast mode still keeps scores from 0.25 and draws a weak name as `object`. Accurate mode stays 0.45.
- The locked stroke is still white. Center-crop cover math is unchanged. If expanded Live View letterboxes or rotates the camera image, the box can miss the object.
- Emulator jobs in `.github/workflows/visor-tracker-android.yml` (`install-android16-with-legacy-present`, `detector-simulation`) need KVM. They were not run locally. A failure that is only a missing emulator or an arm64 release APK on an x86_64 emulator is an external blocker when the JVM unit-test job passed.

## Resume

If this pass is interrupted before the branch is pushed and the Actions run for that SHA is recorded:

```
cd C:\Users\Admin\cloud-ai-os-visor-tracker
git status
git rev-parse HEAD
git rev-parse origin/feat/visor-overlay-phase1
```

Unit tests and both APK builds above already passed. Do not reset or discard the uncommitted Kotlin and `docs/visor-checkpoints` edits. From `experiments/visor-tracker-android`, with `ANDROID_HOME` set as above, re-run `:app:testDebugUnitTest` only if source changed after this note. Then commit the Kotlin, tests, and checkpoint markdown (not `app/build/`, `.gradle/`, or `local.properties`), `git push origin HEAD`, and `gh run list --commit <SHA> --repo mattumali579/cloud-ai-os`.
