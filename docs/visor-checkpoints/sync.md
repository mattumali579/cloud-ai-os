# Visor overlay phase 1 sync

Fast-forward only succeeded. Local `feat/visor-overlay-phase1` moved from `9406f82b9d1e230a320ea10cf602dd407678d577` to `bcad12477e9a522b13f74090781a250a5f7aeaa6`.

Branch status after pull: `feat/visor-overlay-phase1...origin/feat/visor-overlay-phase1` (in sync with origin; not ahead, not behind).

Untracked build outputs were left in place and were not committed:

- `experiments/visor-tracker-android/.gradle/`
- `experiments/visor-tracker-android/app/build/`
- `experiments/visor-tracker-android/build/`
- `experiments/visor-tracker-android/release-apks/`
- `experiments/visor-tracker-android/signed-apk/`

## Commits pulled (8)

Range: `9406f82b9d1e230a320ea10cf602dd407678d577..bcad12477e9a522b13f74090781a250a5f7aeaa6`

- `bcad124` Correct Visor Tracker release notes for one-box targeting
- `40acc67` Prepare Visor Tracker 0.23 for the verified detector fixes
- `76d39bf` Allow centered pre-lock target in simulation
- `c77343f` Test Visor production A-B-A target handoff
- `a99ab04` Enable KVM for the detector emulator boot
- `ec7c123` Capture emulator logcat when detector simulation crashes
- `310c58b` Enable Visor emulator and detector CI jobs
- `3cc4656` Disable long-running Visor emulator CI jobs at owner's request

## Changed files

```
 .github/workflows/visor-tracker-android.yml        |  8 +-
 .../visor-tracker-android/app/build.gradle.kts     |  4 +-
 .../visortracker/DetectorOverlaySimulationTest.kt  | 91 +++++++++++++++-------
 .../scripts/run-detector-simulation.sh             | 12 ++-
 4 files changed, 84 insertions(+), 31 deletions(-)
```

- `.github/workflows/visor-tracker-android.yml` — KVM udev rule, emulator boot timeout 900s, release notes now describe one selected object box (commits `3cc4656`, `310c58b`, `a99ab04`, `bcad124`)
- `experiments/visor-tracker-android/app/build.gradle.kts` — `versionCode` 22 → 23, `versionName` `0.22.0` → `0.23.0` (`40acc67`)
- `experiments/visor-tracker-android/scripts/run-detector-simulation.sh` — dump logcat and fail unless instrumentation reports `OK (2 tests)` (`ec7c123`)
- `experiments/visor-tracker-android/app/src/androidTest/java/com/mattumali/visortracker/DetectorOverlaySimulationTest.kt` — production A-B-A handoff now goes through `TrackingHandoff` and `TrackingOverlayView` (`c77343f`, `76d39bf`)

## Owned source files touched

None. The remote commits did not modify these Kotlin sources:

Camera frames / detection: `VisionPipeline`, `DetectorSchedule`, `YuvSnapshot`, `DetectionPreset`, `LabelPolicy`, `StageFaults`

Lock / selection: `AimLock`, `TrackingHandoff`, `NearbyCandidates`, `MotionTracker`, `TrackerUpdater`, `BoxHold`, `TrackIds`, `RelativeDistanceEstimator`, `LockMetrics`

Overlay: `TrackingOverlayView`, `OverlayTrackingService`, `OverlayDiagnostics`, `MainActivity`, `CoverGeometry`, `PipelineStats`, `TrackerQuickTile`

## Merge risk

No git conflict with those owned sources. Editing them will not overlap the eight pulled commits.

The instrumentation test now encodes a production contract those sources must keep:

- `productionSelectionSwitchesAtoBAndBackOnlyAfterTwoDetectorFrames` drives `TrackingOverlayView.observeCandidates`, `TrackingHandoff.offer` / `pump`, then `TrackingOverlayView.updateFrame`.
- The overlay must draw exactly one box.
- `TrackingHandoff` must publish the track id the overlay has selected.
- A centered target is acquired on the normal lock path. Switching A→B, and back B→A, happens only on the second detector frame where the new target is centered. The first frame of the new center keeps the previous lock.

`AimLock` is no longer called directly by that test. Changes to `AimLock`, `TrackingHandoff`, `TrackingOverlayView`, or the one-box selection path can fail `DetectorOverlaySimulationTest` without a merge conflict.

Gradle and CI were updated and should be left alone by source workers: app version is now `0.23.0` (`versionCode` 23), and the detector emulator job expects exactly two instrumentation tests to pass.
