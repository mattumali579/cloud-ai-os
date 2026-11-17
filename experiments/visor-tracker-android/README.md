# Visor Fast Tracking (Android)

Free on-device object/hand/face detection with labeled boxes rendered **over the phone's full-screen Visor Live View**. Compatible target: Samsung Galaxy S22 Ultra (Android 16) and Ray-Ban Meta using Visor.

**This is not an AR display inside normal Ray-Ban Meta glasses:** they have no display. Visor's existing video remains in its own app; the Android companion is a transparent, touch-through overlay.

## Architecture

- Official **Visor Development Kit (VDK) v0.1.1** from Maven Central: `com.macedonga.visor:kit:0.1.1`.
- User grants `Scope.CAMERA` from Visor's consent screen; no Developer Mode token or separate local server needed.
- `Visor.camera.live(LiveConfig { quality = LOW; frameRate = 30; audio = false })` joins the existing stream when one runs. On a new stream, 30 FPS is **requested**, not guaranteed. Visor picks the actual rate and size when Live View was already running.
- `LiveVideoDecoder` decodes HEVC/H.264 on Android MediaCodec, returning hardware-decoded YUV frames. `YuvSnapshot` copies selected frames to bounded 384px (Fast) or 512px (Higher accuracy) RGB for MediaPipe without blocking the video decoder.
- Object detection: **Fast mode** (EfficientDet-Lite0, 320×320 model, score floor 0.50, up to 8 candidates) or **Higher accuracy** (EfficientDet-Lite2, 448×448 model, score floor 0.45, up to 8 candidates). The overlay draws at most **five** of those candidates. Optional hands and faces are staggered and cannot take an object slot while five objects are shown. Slow AI frames are dropped rather than queued. The detector's maximum requested cadence is approximately 15 Hz, but inference may be slower on hardware.
- `MotionTracker` uses inexpensive grayscale block matching to follow box motion on intervening decoded frames, bounded to avoid stale boxes. This is lightweight image tracking, **not** 30 AI detections per second.
- `TrackingOverlayView` draws transparent boxes with cover/crop transform matching Visor's expanded Live View. The Android user-started foreground service keeps tracking alive during app switching; the Stop notification terminates it.
- The notification displays measured **video FPS, AI FPS, fresh box FPS, draw FPS, box age, 95th-percentile age, and dropped frames**. Results depend on heat, camera quality, movement and phone hardware.

The old 5-FPS `LIVE_FRAMES` JPEG service is no longer used by this version. VDK streaming can deliver higher frame rates; real-world alignment and sustained FPS **must still be measured on the actual phone**.

## Version 0.11: five nearby candidates, stabler names, same calibration

The overlay draws at most five object boxes. The on-screen caption is **5 nearby candidates (visual estimate)**. That rank is not measured distance: one forward camera and one manual calibration cannot assign feet to five objects of different real sizes. Lower in the frame is treated as more likely to be near the wearer, and apparent size is compared only within the same label. Membership stays put unless a newcomer leads for several frames. The calibrated track keeps its slot while it is still tracked.

Unsure names are drawn as `object`. A phone/laptop near-tie, a weak laptop score, or a middling scissors score (the COCO stand-in when the model has no glasses class) is not promoted. A specific label already on a track is not erased by those uncertain frames. Feet still use `D = D0 × (h0 / h)` for that one track id only. If the id leaves the frame, the overlay says distance is unavailable and does not give the old calibration to a neighbor.

Same package `com.mattumali.visortracker.stable`, same signing configuration, versionCode 11. Fast mode stays EfficientDet-Lite0. Physical Live View FPS on the Galaxy S22 Ultra is not measured in CI.

## Version 0.10: Play Protect can finish the scan

Play Protect was calling the download harmful because the APK asked to install other packages, including a silent-update permission a normal app cannot use. Version 0.10 removes those. It is arm64 only, for the S22 Ultra, and smaller so the scan can finish. Same package and same signing certificate. Download **Visor Fast Tracking 10**. If a warning remains after the scan, open More details and choose Install anyway.

## Version 0.9: download installs on the Samsung

The file on the GitHub release is a normal release build of `com.mattumali.visortracker.stable`, signed with the same certificate as v0.6 and v0.8. v0.8 was a debuggable APK with uncompressed native libraries, which Samsung reports only as "App not installed." Download **Visor Fast Tracking 9**, not the older file still sitting in Downloads.

## Version 0.8: boxes on full-screen Live View, fresh positions

Same package `com.mattumali.visortracker.stable` and the same signing configuration as v0.7. The overlay window is full-screen, fully opaque, and touch-through, using the same cover crop as Visor's expanded Live View. Grayscale motion follows head turns out to 16 pixels per frame on the 192-pixel motion image and drops a box when the match is lost instead of leaving it frozen. The notification reports video FPS, AI FPS, fresh box-coordinate FPS, draw FPS, and box age. A 60 Hz redraw of an unchanged box is not counted.

Hardware proof still requires the Samsung phone: wireless debugging was not connected from this PC, so v0.8 is not claimed as glasses-verified.

## Version 0.7: offline distance estimate for a calibrated object

The existing tracking overlay now supports a **single-object approximate distance**.
It needs NO internet, cloud inference, new ML model, or additional app. It is
**not** depth sensing and does not know every object's size. Its measurement
only applies to the same physical target until tracking stops or the stream is
lost, because the camera has no reliable absolute 3D scale.

1. Start Tracker and Visor Live View as usual.
2. Measure the real starting distance between the **glasses camera** and a
   stationary, fully visible COCO-detectable object (for example, a chair).
3. In Tracker, set **Starting distance in feet** to that measurement (defaults to
   6 ft). Alternatively, prepare the distance in Tracker beforehand.
4. While aiming the **glasses at the center of that object**, use
   **CALIBRATE CENTER OBJECT** in Tracker or **Calibrate** on its ongoing
   foreground notification. The latter works while keeping Live View open.
5. The selected tracking box adds a label such as `chair #4 ~6.0 ft` and updates
   as the target's visible box grows/shrinks. Other object boxes have no distance.
6. Recalibrate when the object rotates significantly, is partly hidden, the
   tracker switches IDs, or the camera feed restarts. Do not interpret this
   value as precise rangefinding or use it for collision safety.

Distance uses **D = D0 × (h0 / h)** with image-height fractions to handle
different frame resolutions. Temporal smoothing reduces flicker but also
slightly delays changes. The box must continue to represent the same
physical object's full visible height; if its shape changes, so does the
estimate. Pure Kotlin tests cover calibration, scale, invalid measurements,
and cross-object isolation.

**Android identity unchanged:** `com.mattumali.visortracker.stable`, using
the same permanent signing configuration as v0.6. The app versionCode is
incremented to 7 so the existing updater can accept a correctly signed update.

## Version 0.6: one-time replacement for Android signing conflict

The published v0.5 signed APK **successfully installs on clean Android 15 and Android 16 emulators**, but a Samsung phone still showed only "App not installed". That phone's underlying PackageInstaller failure code has NOT been captured.

This version is built from the SAME existing cloud-ai-os Tracker project with the SAME permanent signing key and all previous tracking/updater functionality. To bypass a possible lingering v0.3 package with the LOST signing certificate (possibly installed in Samsung Secure Folder or another profile), v0.6 uses a **new stable Android package ID**: `com.mattumali.visortracker.stable`. This is technically a **new Android app identity**, not an in-place upgrade. There may be two Tracker entries if the old package is still installed; remove the old Tracker only after confirming the new app works. Do not uninstall Visor.

Versions v0.6 and later **MUST NEVER change this package ID or signing key again**. Newer signed versions will update the installed stable-package app normally through the built-in updater. Android can still require user approval and Samsung Auto Blocker may block sideloading. The new ID only avoids old package-signature conflicts; it cannot bypass device installation restrictions.

## Install / run

1. Keep **Visor** installed and set up on the glasses.
2. Install the **v0.6 stable replacement** (`com.mattumali.visortracker.stable`) once. This is the same Tracker codebase but a new Android app identity required to isolate the lost v0.3 signing certificate. After it works, remove any obsolete Tracker v0.3 icon; leave Visor installed.
3. Open Visor Fast Tracking and tap **Allow display over other apps**; enable it in Android Settings.
4. Tap **Grant Visor camera access**; on Visor's consent screen allow **Camera** for the companion app.
5. For best responsiveness, leave **Also detect hands and faces** unchecked. Toggle **Higher accuracy** if you want Lite2's increased recognition quality, understanding that AI FPS and battery life may be worse. The setting takes effect at the next tracking start.
6. Tap **START FAST OVERLAY** and open Visor's Live View, fully expanded (not mini-window/stepped).
7. Add **Visor Tracker** to Samsung Quick Settings once (swipe down twice, tap Edit, add Visor Tracker). Start Live View inside Visor, then toggle tracking from Quick Settings without switching apps. Check bounding boxes directly over the Visor live picture and read **Video / AI / boxes FPS** from the tracker foreground notification. Stop with the notification's **Stop** action or reopen the companion.

If permissions were denied, go to Visor **Settings → Apps** to review the companion app's Camera scope. The overlay itself also needs Android's permission to draw over other apps. A camera authorization failure is not fixed by pasting a Developer Mode token.

## Build

Isolated app build; the rest of the Jarvis repo is untouched:

- Android SDK API 37.0 (canary SDK channel), JDK 17
- Gradle 9.6.0, Android Gradle Plugin 9.4.1, Kotlin Gradle Plugin 2.4.20 (aligned with the VDK)
- Local command: `gradle --no-daemon assembleDebug testDebugUnitTest`
- APK: `app/build/outputs/apk/debug/app-debug.apk`
- CI: `.github/workflows/visor-tracker-android.yml`

GitHub Actions artifacts are ZIP files containing the APK. Extract `app-debug.apk` before installing; do not attempt to install the ZIP.

### Signing limitation (historical; previous installed app identity)

Earlier GitHub Actions produced temporary debug signing keys per runner. Permanent signing is now configured in protected Actions secrets. Android only accepts APK updates signed by the **same private key**. A package-name change is an install-as-new workaround, not an update fix. To support seamless future updates, the user must provide a secure persistent signing keystore through CI secrets or sign repeat builds locally with a single securely stored key. **Do not commit signing keys to GitHub.**

## Verification ladder

- **Source verification**: Visor's VDK `Camera.live`, `LiveVideoDecoder`, scope permissions, and `KitLive.watch` support this design.
- **Automated**: Gradle build plus JVM tests for box projection, tracking IDs, RGB/YUV conversion and grayscale motion. See CI result.
- **Device (not yet verified)**: real Visor Camera scope grant, VDK subscription while native Live View is shown, Android 16 overlay placement/touch-through, device FPS and thermals. Do not claim 30-FPS tracking from CI alone.

VDK source and official docs:
- https://github.com/macedonga/visor-kit/blob/main/android/src/main/kotlin/com/macedonga/visor/kit/Camera.kt
- https://github.com/macedonga/visor-kit/blob/main/android/src/main/kotlin/com/macedonga/visor/kit/LiveDecoders.kt
- https://github.com/macedonga/visor/blob/main/app/src/main/kotlin/com/macedonga/visor/outside/KitLive.kt
- https://visor.macedonga.com/developers/docs/camera-and-captures/


## Accuracy / stability upgrade

Version 0.4 preserves the same app ID as 0.3, adds EfficientDet-Lite2 optional
accuracy mode, the lower-confidence detection and stable IDs from the preceding
commits, and the one-tap Quick Settings toggle. It does not magically recognize
arbitrary unsupported categories; both EfficientDet models know COCO's 80 classes.
Try the higher accuracy mode when objects are missed; return to Fast when the phone
gets hot or AI FPS becomes too low.

**Do not uninstall v0.3 merely because this source changed.** GitHub Actions
debug builds use runner-specific signing keys, which Android cannot use for an
in-place update over a differently signed v0.3 installation. The original private
signing key cannot be recovered from an APK. A stable signer is required before
we can promise seamless updates; if the old private key is lost, a one-time
signing migration is unavoidable. Never publish a signing keystore.


## Future automatic updates (v0.5+)

- From v0.6 onward keep the exact new stable Android package ID `com.mattumali.visortracker.stable`.
- Sign each shipped version using the SAME private Android signing key (protected GitHub Actions secrets).
- A GitHub Actions tag `visor-tracker-vN` (N = Android `versionCode`) publishes an APK asset named `Visor-Fast-Tracking.apk` to the public GitHub Release **only after Android tests pass** and stable signing is configured.
- The app has a built-in updater: on app launch and periodically on **unmetered Wi-Fi**, it queries only published `visor-tracker-vN` releases, verifies the APK size, optional GitHub SHA-256 digest, package identity, versionCode, and lets Android PackageInstaller verify the signature. It never accepts an APK with a different package ID.
- One-time: enable **Allow from this source** in Android settings when prompted. The app requests Android's supported self-update without user interaction, but will display a system approval notification if Samsung requires confirmation. No root, USB, separate updater APK, or arbitrary Android permission bypass.
- Automatic updates are toggleable in the Tracker app. The updater delays installation while tracking is active to avoid interrupting Live View. Android may defer periodic checks for network and battery.
- **One-time migration only**: installed v0.3 was signed with a lost ephemeral debug key. A v0.5+ package built with a different permanent key cannot install over v0.3; the old APK must be replaced once. After that use only the permanent signing key, never reinstall for routine updates.

Security: GitHub Releases must be created from tested source and the protected signing key must NEVER be committed to the repository or bundled inside the APK. GitHub Releases from other components of cloud-ai-os are ignored by the Tracker updater.
