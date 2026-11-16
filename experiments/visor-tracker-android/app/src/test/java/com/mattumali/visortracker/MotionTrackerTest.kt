package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test

class MotionTrackerTest {
    // Bright checkerboard patch over dark background: shifts by 2 px each frame.
    private fun patterned(dx: Int): GrayFrame {
        val width = 100
        val height = 80
        val pixels = ByteArray(width * height) { 25 }
        for (y in 30 until 50) for (x in 30 + dx until 50 + dx) {
            pixels[y * width + x] = (65 + ((x - dx) * 37 + y * 19 + (x - dx) * (y + 11) * 7) % 175).toByte()
        }
        return GrayFrame(width, height, pixels)
    }

    @Test fun followsTranslatedTexturedObject() {
        val tracker = MotionTracker()
        tracker.reset(FrameDetections(100, 80, listOf(
            BoxObservation("object", "box", 0.9f, 30f, 30f, 50f, 50f, trackId = 3)
        )), patterned(0))
        val moved = tracker.advance(patterned(2))
        assertNotNull(moved)
        assertEquals(3, moved!!.boxes.first().trackId)
        assertEquals(32f, moved.boxes.first().left, 1.1f)
    }

    @Test fun expiresStaleDetections() {
        val tracker = MotionTracker(maxPredictionFrames = 1)
        tracker.reset(FrameDetections(100, 80, listOf(
            BoxObservation("object", "box", 0.9f, 30f, 30f, 50f, 50f)
        )), patterned(0))
        tracker.advance(patterned(0))
        tracker.advance(patterned(0))
        assertEquals(null, tracker.advance(patterned(0)))
    }
}
