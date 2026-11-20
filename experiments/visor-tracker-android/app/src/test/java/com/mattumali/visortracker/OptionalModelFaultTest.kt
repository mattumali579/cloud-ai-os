package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A hand or face failure is kept off the object-detection exception path.
 * The notification only shows it after the service copies lastOptionalFault
 * into the stage summary. No device, clock, or sleep.
 */
class OptionalModelFaultTest {
    @Test fun aHandFaultReachesTheNotificationAfterObjectsSucceed() {
        val faults = StageFaults()
        faults.fail("detect", "Decode/detection failed: model hung")
        faults.clear("detect")
        OptionalModelFault.apply(faults, "hands: model hung")
        assertEquals("hands: model hung", faults.summary())

        val snapshot = trackingSnapshot()
        val diagnosis = OverlayDiagnostics.diagnose(snapshot)
        assertEquals(OverlayHop.TRACKING, diagnosis.hop)
        val extra = OptionalModelFault.notificationExtra("V30", "video 29", faults.summary())
        val line = OverlayDiagnostics.notificationLine(snapshot, diagnosis, extra)
        assertTrue(line.contains("TRACKING"))
        assertTrue(line.contains("hands: model hung"))
        assertTrue(line.contains("V30"))
    }

    @Test fun aCleanOptionalScanClearsTheNotificationFault() {
        val faults = StageFaults()
        OptionalModelFault.apply(faults, "faces: model hung")
        assertEquals("faces: model hung", faults.summary())
        OptionalModelFault.apply(faults, null)
        assertEquals("", faults.summary())
        val extra = OptionalModelFault.notificationExtra("V30", "", faults.summary())
        assertEquals("V30", extra)
        assertFalse(extra.contains("faces:"))
    }

    @Test fun aFaceFaultDoesNotEraseAGrayscaleFault() {
        val faults = StageFaults()
        faults.fail("gray", "capture failed")
        OptionalModelFault.apply(faults, "faces: landmark missing")
        assertEquals("gray: capture failed; faces: landmark missing", faults.summary())
        val extra = OptionalModelFault.notificationExtra("", "", faults.summary())
        assertEquals("faces: landmark missing", extra)
    }

    private fun trackingSnapshot() = OverlaySnapshot(
        versionName = "0.23.0",
        versionCode = 23,
        packageName = "com.mattumali.visortracker.stable",
        overlayPermission = true,
        serviceRunning = true,
        overlayAttached = true,
        cameraGranted = true,
        liveConnected = true,
        framesReceived = 30,
        grayFrames = 30,
        inferencesCompleted = 4,
        rawDetections = 1,
        freshBoxes = 1,
        drawnBoxes = 1,
        boxAgeMs = 12,
    )
}
