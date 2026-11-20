# Visor Tracker release rule

This is the required ship path. Do not wait on emulator jobs.

## Always do this

1. Bump `versionCode` to N and `versionName` in `experiments/visor-tracker-android/app/build.gradle.kts`. The Git tag must be `visor-tracker-vN` with the same N.
2. Run JVM unit tests (`testDebugUnitTest`).
3. Commit, push the branch, and push tag `visor-tracker-vN`.
4. CI job `build-and-test` signs the APK and uploads artifact `visor-tracker-apk`.
5. CI job `publish-release` depends **only** on `build-and-test`. It publishes `Visor-Fast-Tracking.apk` to GitHub Releases as soon as that job succeeds.
6. Tell the owner to tap **UPDATE THIS APP**. If that hangs, use **OPEN RELEASE PAGE** or  
   `https://github.com/mattumali579/cloud-ai-os/releases/download/visor-tracker-vN/Visor-Fast-Tracking.apk`  
   Confirm **Update**. Do not uninstall.

## Never do this

- Do not make `publish-release` wait on `detector-simulation`, `install-android16-with-legacy-present`, or any other emulator job.
- Do not tell the owner the phone update is blocked until emulators finish.
- Do not leave **UPDATE THIS APP** with a single GitHub API path and no fallback.
- Do not claim the update is on the phone until the Release asset exists.

## Fallback when GitHub stalls

- The app probes direct download URLs for the next versionCodes if the releases API is slow or fails.
- The Tracker screen has **OPEN RELEASE PAGE**.
- If CI has already built the signed artifact but has not created the Release yet, download artifact `visor-tracker-apk` and `gh release create visor-tracker-vN Visor-Fast-Tracking.apk`. Do not wait for the emulator workflow.

Emulator jobs may still run in parallel. They are extra checks. They are not the ship gate.
