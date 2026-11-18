#!/usr/bin/env bash
# One process. The emulator runner starts each workflow script line in a new
# shell, so a cd on its own line does not apply to adb install.
set -euo pipefail

adb wait-for-device
adb install -r experiments/visor-tracker-android/app/build/outputs/apk/debug/app-debug.apk
adb install -r experiments/visor-tracker-android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk

mkdir -p "${RUNNER_TEMP}/detector-sim"
log="${RUNNER_TEMP}/detector-sim/instrument.txt"
set +e
timeout 480s adb shell am instrument -w -r \
  com.mattumali.visortracker.stable.test/androidx.test.runner.AndroidJUnitRunner \
  | tee "$log"
code=${PIPESTATUS[0]}
set -e

# Keep device-side crash details even when the instrumentation process exits
# without a useful stack trace. The job uploads this directory on failure.
adb logcat -d -v threadtime > "${RUNNER_TEMP}/detector-sim/logcat.txt"
adb exec-out run-as com.mattumali.visortracker.stable cat files/detector-stage.txt \
  > "${RUNNER_TEMP}/detector-sim/detector-stage.txt" || true
adb exec-out run-as com.mattumali.visortracker.stable cat files/replay-stage.txt \
  > "${RUNNER_TEMP}/detector-sim/replay-stage.txt" || true
adb exec-out run-as com.mattumali.visortracker.stable cat files/overlay-sim.png \
  > "${RUNNER_TEMP}/detector-sim/overlay-sim.png" || true
if [[ "$code" -ne 0 ]] || ! grep -q "OK (3 tests)" "$log"; then
  echo "Instrumentation did not report OK (3 tests); collecting crash context."
  grep -E -i "FATAL EXCEPTION|AndroidRuntime|Fatal signal|tombstoned|crash_dump|Process:|Caused by:|OutOfMemoryError|SIGABRT|SIGSEGV|MediaPipe|TFLite" \
    "${RUNNER_TEMP}/detector-sim/logcat.txt" | tail -250 || true
  exit 1
fi

test -s "${RUNNER_TEMP}/detector-sim/detector-stage.txt"
test -s "${RUNNER_TEMP}/detector-sim/replay-stage.txt"
