# Visor Fast Tracking (Android)

Free on-device object/hand/face detection with labeled boxes rendered **over the phone's full-screen Visor Live View**. Compatible target: Samsung Galaxy S22 Ultra (Android 16) and Ray-Ban Meta using Visor.

**This is not an AR display inside normal Ray-Ban Meta glasses:** they have no display. Visor's existing video remains in its own app; the Android companion is a transparent, touch-through overlay.

## Version 0.30: depth-angled finger recognition

Version code 31. MediaPipe hand landmark `z` is now preserved and used for the index-extension and straightness checks. When the index points mostly toward or away from the camera, the tracker uses the projected fingertip as a stable depth cursor instead of rejecting the foreshortened finger or inventing a noisy 2D direction. Ordinary side-on pointing keeps the existing 2D ray.

This does **not** add object depth. EfficientDet still supplies 2D boxes, so two objects overlapping at the fingertip remain ambiguous. The Three.js depth lab can show the ray-to-cursor transition, but only a signed S22 Ultra build in Visor Live View can prove camera behavior.

## Version 0.29: hand landmarks win over a false person label

Version code 30. Same package `com.mattumali.visortracker.stable` and the same protected signing path.

MediaPipe's 21-point hand result is now authoritative when EfficientDet labels the same hand-sized region as `person`. A genuine full-body person box remains; only a strongly overlapping, hand-sized false positive is hidden. An extended index is labeled `pointing hand`, and the overlay connects the 21 landmarks into the standard hand skeleton so hand movement and pointing are visible instead of being covered by a generic object label. The 2D pointing ray and one-object lock behavior are unchanged. JVM tests and a local debug build cover the merge policy; physical S22 Ultra and Visor Live View behavior still require the signed update and an on-device test.

## Version 0.27: reuse a known replacement, and keep the ray 2D

Version code 27. Same package `com.mattumali.visortracker.stable` and the same signing certificate.

An extended index still builds a 2D ray on the camera image. That ray is not measured depth. When the locked object leaves and another object was already in the previous detection, AimLock finishes its two observations on that one detection instead of waiting for a second hand-and-object pass. If nothing else is visible, the lock still holds for 3 frames. A closed fist or a fully curled index does not switch the lock. The same gesture has to stay for 4 frames before the lock releases, and that release does not send a command. Motion still applies the measured shift on the same frame. There is no neural-network training. The phone, Visor Live View, and the glasses were not measured for this version.

On the emulator people.jpg run for commit `414330f` (not a phone), the known-person switch was 1,016 ms in one detect pass. The object model on that pass took 866 ms, so it did not get under 500 ms. The 60 s detect-only rate was 6.41 fps, against 6.45 fps in 0.26.0. The numbers are in [docs/visor-checkpoints/performance.md](../../docs/visor-checkpoints/performance.md).

## Version 0.25: point with the index finger

Version code 25. Same package `com.mattumali.visortracker.stable` and the same signing certificate as 0.23 and 0.24.

MediaPipe Hand Landmarker runs on the CPU delegate with EfficientDet. An extended index finger, with the other fingers curled, builds a 2D ray from the fingertip. The object that ray enters is the lock after two steady frames. The lock stays when the hand drops, follows that track while it moves, and after a short miss takes the next visible object by score, then size, then track id. With no hand landmarks, the center of the picture still locks. The ray is not a 3D direction. Overlapping objects along it stay ambiguous. The phone and glasses are not part of this check.

## Architecture

- Official **Visor Development Kit (VDK) v0.1.1** from Maven Central: `com.macedonga.visor:kit:0.1.1`.
- User grants `Scope.CAMERA` from Visor's consent screen; no Developer Mode token or separate local server needed.
- `Visor.camera.live(LiveConfig { quality = LOW; frameRate = 30; audio = false })` joins the existing stream when one runs. On a new stream, 30 FPS is **requested**, not guaranteed. Visor picks the actual rate and size when Live View was already running.
- `LiveVideoDecoder` decodes HEVC/H.264 on Android MediaCodec, returning hardware-decoded YUV frames. `YuvSnapshot` copies selected frames to bounded 384px (Fast) or 512px (Higher accuracy) RGB for MediaPipe without blocking the video decoder.
- Object detection: **Fast mode** (EfficientDet-Lite0, score floor 0.35, up to 8 candidates) or **Higher accuracy** (EfficientDet-Lite2, score floor 0.45, up to 8 candidates). The overlay draws **one** object. A weak name is shown as `object`. After one object is locked, the detector runs at 4 Hz. Before a lock it runs at 5 Hz.
- `MotionTracker` follows the locked box on the newest grayscale frame. The work slot holds one frame; an older frame still waiting is dropped, not replayed. A detector result is painted only after it has been walked forward onto that newest frame. If the patch match fails, the box is hidden and the same track is searched again for 250 ms. A remembered detector box is not drawn. This is lightweight image tracking, **not** 30 AI detections per second.
- `TrackingOverlayView` draws transparent boxes with cover/crop transform matching Visor's expanded Live View. A corner proof mark is drawn even when there are no boxes. The Android user-started foreground service keeps tracking alive during app switching; the Stop notification terminates it.
- The notification displays measured **decoded video FPS, detector FPS, motion-tracker FPS, changed-box FPS, draw FPS, p50/p95/p99 tracker interval, p50/p95/p99 decoded-frame age, drops, stationary jitter, id switches, and lock losses**. Changed-box FPS does not count a redraw or a hide. Tracker FPS counts frames the motion tracker stepped, including frames where the box did not move. Results depend on heat, camera quality, movement and phone hardware.

The old 5-FPS `LIVE_FRAMES` JPEG service is no longer used by this version. VDK streaming can deliver higher frame rates; real-world alignment and sustained FPS **must still be measured on the actual phone**.

## Version 0.22: let the first detection finish and show any returned box

Version code 22. The phone showed video at about 29 fps and zero detector passes. Object detection now runs on the CPU delegate so that first pass is not left on a device delegate that never returns. Every box EfficientDet-Lite0 returns is drawn. A weak name is `object`. The model still cannot box something it does not recognize.

## Version 0.21: draw a real detection and keep it through a short miss

Version code 21. Fast mode accepts EfficientDet-Lite0 scores from 0.35, and a weak name is drawn as `object` instead of being deleted. An empty motion result cannot erase a box for 2 seconds. The corner reads `det`, `pub`, and `drw`: detector objects, published boxes, and boxes in onDraw. The same `detect` and `publish` lines are stored on the phone. EXPORT DIAGNOSTICS on the Tracker screen shares that file. It does not need USB or wireless ADB, and it remains after Live View switches Wi-Fi.

## Version 0.19: one Update this app button

Version code 19. The overlay draws one object: the one being pointed at, then only that lock. Fast mode stays EfficientDet-Lite0. **UPDATE THIS APP** checks, downloads the signed APK, and opens Android's update screen for this same package. Do not uninstall.

## Version 0.18: point the glasses to lock an object

Version code 18. The object nearest the center of Live View becomes the lock after it stays there for a few frames. Pointing at a different object and holding it there switches the lock and clears the old distance until you calibrate the new one. Movement and box jitter do not swap the lock every frame. The overlay stays touch-through.

## Version 0.17: tap a box to lock it, then update this same app

Version code 17. The notification **Pick** action lets one finger tap choose the object. That tap locks the track. **Calibrate** then measures that object, not whichever box is nearest the center. After the tap, the overlay is touch-through again. **INSTALL VERSION** downloads the signed APK for this package. Android should say Update. Do not uninstall.

## Version 0.16: an Install update button

Version code 16. Check for updates only changed a line of text and hid the download in a notification. When a newer signed release exists, the screen shows **INSTALL VERSION**. That opens the APK. Android still asks you to confirm the install.

## Version 0.15: draw the detection, and keep the window above Visor

Version code 15. Same package and signing certificate.

A detector box is drawn even when grayscale matching cannot follow every frame since that detection. An older detection outside the retained frames is still dropped. The overlay window is created from the application context, so Samsung does not keep it inside the Tracker task after the user opens Visor.

## Version 0.14: prove which hop failed

Version code 14. Same package `com.mattumali.visortracker.stable` and the same signing certificate.

v0.13 attached a transparent window and drew nothing until a box existed, so a denied permission, a missing Visor stream, and zero detections looked the same on Live View. v0.14 draws a small green corner mark as soon as the window is attached. The mark reads `v0.14.0` and the first failed hop: `OVERLAY BLOCKED`, `NO CAMERA FRAMES`, or `0 OBJECTS DETECTED`. The same line is in the ongoing notification, with the package name and versionCode. The mark is touch-through, stays in the corner, and can be turned off with **Show the Live View proof mark**. The app screen shows the package and version so it cannot be confused with the old 0.1.0 Tracker, which has no Check for updates button.

Visor's own Live View is a TextureView inside Visor. This build does not claim the mark is visible on the Galaxy S22 Ultra until someone looks at expanded Live View. If the notification says the window is attached and the mark is missing on that picture, the phone is not drawing the overlay above Visor.

`experiments/visor-tracker-android/scripts/Sync-VisorTrackerBranch.ps1` fast-forwards `C:\Users\Admin\cloud-ai-os-visor-tracker` to this branch. It refuses a dirty tracked tree, does not change git remotes, and does not reset or delete files.

## Version 0.13: locked box at camera rate, detector at 4 Hz

Version code 13. Same package `com.mattumali.visortracker.stable` and the same signing certificate. No release tag was published with this change.

The motion tracker is the position path. It steps the newest decoded gray frame and drops a frame that is still waiting when a newer one arrives. Object recognition stays at 5 Hz until a target is locked, then 4 Hz. A detector box is drawn only after block matching walks it onto the newest frame. If that walk cannot be done, the old coordinates are not shown. A match that fails hides the box. The same track is searched with a wider window for 250 ms, then it stays hidden until detection finds that same id again. Feet stay on that id. A redraw of the same camera frame does not move the feet. The notification's `trk` rate is frames the tracker stepped. `box` is a coordinate or label change. Hiding a lost lock does not increment `box`.

These are engineering targets for a 30 FPS decode, not measurements from the Galaxy S22 Ultra: tracker steps on at least 90% of decoded frames, p95 spacing at most 50 ms, decoded-frame-to-box p95 at most 50 ms and p99 at most 100 ms, stationary center jitter at most 1% of the box-coordinate frame width. A unit test feeds frames 33 ms apart through the handoff. On that fixture, 20 of 20 frames were stepped, interval p50/p95/p99 was 33/33/33 ms, and age was 8/8/8 ms because the test clock itself adds 8 ms. A stationary fixture held jitter at 0. Skipping every other frame produced a 66 ms interval and was asserted to miss the 50 ms target, so a drop is not reported as a pass. On this JVM a locked-box motion step took about 0.2 ms and the 1280×720 to 384 YUV loop about 1 ms. That does not include Visor, the phone, thermal throttling, or glasses-to-screen delay. The 5-minute heat test was not run.

Monocular distance is still `D = D0 × (h0 / h)` for one object. Similar-triangles cases at 3, 6, and 10 ft are about 0% error when box height scales as `1/distance`. An 8% height error is about 7.4% distance error. A 35% shorter box from tilt is about 53.8% distance error and is not treated as accurate. There is no depth sensor.

## Version 0.12: one calibrated distance, less replay lag

Version code 12. Same package `com.mattumali.visortracker.stable` and the same signing certificate.

Distance is still one track. Enter measured feet, aim that object at the center of Live View, and use Calibrate on the notification or in the app. The notification confirms the reference feet. Only that track id then shows approximate feet with `D = D0 × (h0 / h)`. A real change in apparent height shows on the next frame. A small jitter is smoothed. If the track leaves the frame, the overlay says **distance unavailable** and does not give those feet to another object. Tilt, rotation, and occlusion are not distance. Tests at 3, 6, and 10 ft are exact only when the box height scales as similar triangles; a 35% height change from tilt is more than 25% distance error. There is no depth sensor.

The gray queue keeps six frames. Motion still steps those frames, then publishes the newest box once, so a backlog is not drawn as a trail of old positions. A detector result older than 300 ms that can no longer be walked onto the retained frames is dropped. Grayscale sampling no longer allocates a full-size luma copy on the decoder thread. YUV plane buffers are reused. The notification adds median milliseconds for grayscale (`g`), YUV-to-bitmap (`y`), MediaPipe (`ai`), the motion batch (`m`), and detection age (`d`). Changed-box FPS counts a new coordinate or label, not a redraw of the same box. Hands and faces stay off unless the checkbox is on. Fast mode remains EfficientDet-Lite0. The on-screen caption is **up to 5 visual candidates**. Weak `object` labels do not fill the five slots. This is not a claim about the five nearest objects.

The v11 tag's publish job failed with `fatal: not a git repository` because `gh release create` ran without a checkout or `--repo`. That tag was not moved. v0.12's publish job checks out the repo, passes `--repo`, and uploads again if the release already exists.

Physical Live View on the Galaxy S22 Ultra is still not measured here.

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
