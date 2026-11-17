package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OverlayDiagnosticsTest {
    private fun live(edit: OverlaySnapshot.() -> OverlaySnapshot = { this }) = OverlaySnapshot(
        versionName = "0.14.0",
        versionCode = 14,
        packageName = "com.mattumali.visortracker.stable",
        overlayPermission = true,
        serviceRunning = true,
        overlayAttached = true,
        cameraGranted = true,
        liveConnected = true,
        framesReceived = 30,
        inferencesCompleted = 4,
        rawDetections = 0,
        freshBoxes = 0,
    ).edit()

    @Test fun overlayPermissionBeatsAMissingStream() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(overlayPermission = false, framesReceived = 0, liveConnected = false, cameraGranted = null)
        })
        assertEquals(OverlayHop.OVERLAY_BLOCKED, diagnosis.hop)
        assertEquals(OverlayDiagnostics.CODE_BLOCKED, diagnosis.code)
    }

    @Test fun addViewFailureStaysBlockedAfterTheServiceStops() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(serviceRunning = false, overlayAttached = false, overlayError = "BadTokenException")
        })
        assertEquals(OverlayHop.OVERLAY_BLOCKED, diagnosis.hop)
        assertTrue(diagnosis.detail.contains("BadTokenException"))
    }

    @Test fun runningWithoutAWindowIsBlocked() {
        val diagnosis = OverlayDiagnostics.diagnose(live { copy(overlayAttached = false) })
        assertEquals(OverlayHop.OVERLAY_BLOCKED, diagnosis.hop)
    }

    @Test fun stoppedServiceIsNotCalledACameraFailure() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(serviceRunning = false, overlayAttached = false, framesReceived = 0)
        })
        assertEquals(OverlayHop.SERVICE_STOPPED, diagnosis.hop)
    }

    @Test fun cameraDenialBeatsNoFrames() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(cameraGranted = false, liveConnected = false, framesReceived = 0)
        })
        assertEquals(OverlayHop.CAMERA_DENIED, diagnosis.hop)
    }

    @Test fun mediaPipeFailureIsNotNoCameraFrames() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(
                cameraGranted = null,
                liveConnected = false,
                framesReceived = 0,
                inferencesCompleted = 0,
                failure = "MediaPipe failed: model missing",
            )
        })
        assertEquals(OverlayHop.DETECTOR_FAILED, diagnosis.hop)
        assertTrue(diagnosis.detail.contains("model missing"))
    }

    @Test fun streamExceptionIsShownWhenNoFrameArrived() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(framesReceived = 0, inferencesCompleted = 0, failure = "VDK stream failed: wifi_off")
        })
        assertEquals(OverlayHop.STREAM_FAILED, diagnosis.hop)
        assertTrue(diagnosis.detail.contains("wifi_off"))
    }

    @Test fun connectedDecoderWithZeroFramesIsNoCameraFrames() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(framesReceived = 0, inferencesCompleted = 0)
        })
        assertEquals(OverlayHop.NO_CAMERA_FRAMES, diagnosis.hop)
        assertEquals(OverlayDiagnostics.CODE_NO_FRAMES, diagnosis.code)
    }

    @Test fun framesBeforeTheFirstInferenceAreNotZeroObjects() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(inferencesCompleted = 0, rawDetections = 0, freshBoxes = 0)
        })
        assertEquals(OverlayHop.WAITING_FOR_DETECTOR, diagnosis.hop)
    }

    @Test fun finishedInferenceWithNothingToDrawIsZeroObjects() {
        val diagnosis = OverlayDiagnostics.diagnose(live())
        assertEquals(OverlayHop.ZERO_OBJECTS, diagnosis.hop)
        assertEquals(OverlayDiagnostics.CODE_ZERO, diagnosis.code)
    }

    @Test fun unmatchedDetectionsAreHiddenRatherThanAbsent() {
        val diagnosis = OverlayDiagnostics.diagnose(live { copy(rawDetections = 2, freshBoxes = 0) })
        assertEquals(OverlayHop.BOXES_HIDDEN, diagnosis.hop)
    }

    @Test fun visibleBoxesWinOverAnOldFailure() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(freshBoxes = 1, boxAgeMs = 40, failure = "VDK stream failed: stopped")
        })
        assertEquals(OverlayHop.TRACKING, diagnosis.hop)
        assertTrue(diagnosis.detail.contains("40"))
    }

    @Test fun proofMarkNamesTheVersionAndTheHop() {
        val snapshot = live { copy(framesReceived = 0, inferencesCompleted = 0, decoderFps = 0f) }
        val diagnosis = OverlayDiagnostics.diagnose(snapshot)
        val lines = OverlayDiagnostics.probeLines(snapshot, diagnosis)
        assertEquals(listOf("v0.14.0 #14", OverlayDiagnostics.CODE_NO_FRAMES, "det0 pub0 drw0"), lines)
    }

    @Test fun proofMarkCanBeTurnedOff() {
        val snapshot = live { copy(probeEnabled = false, freshBoxes = 2) }
        val diagnosis = OverlayDiagnostics.diagnose(snapshot)
        assertTrue(OverlayDiagnostics.probeLines(snapshot, diagnosis).isEmpty())
        val line = OverlayDiagnostics.notificationLine(snapshot, diagnosis, "V30")
        assertTrue(line.contains("com.mattumali.visortracker.stable"))
        assertTrue(line.contains("0.14.0 (14)"))
        assertTrue(line.contains("TRACKING"))
        assertTrue(line.endsWith("V30"))
    }

    @Test fun connectingIsDistinctFromASilentStream() {
        val connecting = OverlayDiagnostics.diagnose(live {
            copy(cameraGranted = null, liveConnected = false, framesReceived = 0, inferencesCompleted = 0)
        })
        assertEquals(OverlayHop.CONNECTING, connecting.hop)
    }
}
