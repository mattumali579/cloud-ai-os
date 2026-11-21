package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class PrivateFrameCaptureTest {

    @Test
    fun captureWritesMetadataAndClearDeletes() {
        val tempDir = File(System.getProperty("java.io.tmpdir"), "test_visor_captures_${System.currentTimeMillis()}")
        tempDir.mkdirs()

        try {
            val capture = PrivateFrameCapture(storageDir = tempDir, maxCaptures = 5)
            val box = BoxObservation("object", "cup", 0.9f, 10f, 10f, 50f, 50f, trackId = 1)

            capture.captureFrame(null, sequence = 1L, capturedAtMs = 1000L, detections = listOf(box))
            Thread.sleep(100) // Allow async write to finish

            assertTrue("Capture count must be at least 1", capture.captureCount() >= 1)

            val meta = tempDir.listFiles { f -> f.extension == "json" }!!.first()
            val text = meta.readText()
            assertTrue(text.contains("raw_vdk_camera_live_pre_overlay"))
            assertTrue(text.contains("\"track_id\": 1"))

            // User-controlled clear
            assertTrue(capture.clearAllCaptures())
            assertEquals("All captures must be cleared", 0, capture.captureCount())
        } finally {
            tempDir.deleteRecursively()
        }
    }
}
