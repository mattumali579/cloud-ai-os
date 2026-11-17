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
timeout 300s adb shell am instrument -w -r \
  com.mattumali.visortracker.stable.test/androidx.test.runner.AndroidJUnitRunner \
  | tee "$log"
code=${PIPESTATUS[0]}
set -e
grep -q "OK (2 tests)" "$log"
test "$code" -eq 0

adb exec-out run-as com.mattumali.visortracker.stable cat files/detector-stage.txt \
  > "${RUNNER_TEMP}/detector-sim/detector-stage.txt"
adb exec-out run-as com.mattumali.visortracker.stable cat files/overlay-sim.png \
  > "${RUNNER_TEMP}/detector-sim/overlay-sim.png"
test -s "${RUNNER_TEMP}/detector-sim/detector-stage.txt"
