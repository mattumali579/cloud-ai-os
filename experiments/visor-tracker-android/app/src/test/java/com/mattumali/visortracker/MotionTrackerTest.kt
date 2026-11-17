package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
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

    @Test fun followsAHeadTurnBeyondTheOldSixPixelWindow() {
        val tracker = MotionTracker()
        tracker.reset(FrameDetections(100, 80, listOf(
            BoxObservation("object", "box", 0.9f, 30f, 30f, 50f, 50f, trackId = 7)
        )), patterned(0))
        val moved = tracker.advance(patterned(12))
        assertNotNull(moved)
        assertEquals(42f, moved!!.boxes.first().left, 1.1f)
    }

    @Test fun dropsTheBoxWhenThePatchDisappears() {
        val tracker = MotionTracker()
        tracker.reset(FrameDetections(100, 80, listOf(
            BoxObservation("object", "box", 0.9f, 30f, 30f, 50f, 50f, trackId = 4)
        )), patterned(0))
        val lost = tracker.advance(GrayFrame(100, 80, ByteArray(100 * 80) { 40 }))
        assertNotNull(lost)
        assertTrue(lost!!.boxes.isEmpty())
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

    @Test fun unchangedCoordinatesAreNotAFreshBoxUpdate() {
        val box = BoxObservation("object", "chair", 0.9f, 10f, 10f, 40f, 50f, trackId = 2)
        assertFalse(BoxMotion.positionsChanged(listOf(box), listOf(box.copy())))
        assertTrue(BoxMotion.positionsChanged(listOf(box), listOf(box.copy(left = 14f))))
        assertTrue(BoxMotion.positionsChanged(listOf(box), listOf(box.copy(label = "object"))))
        assertFalse(BoxMotion.positionsChanged(listOf(box), emptyList()))
    }

    @Test fun stationaryFramesDoNotDrift() {
        val tracker = MotionTracker()
        val box = BoxObservation("object", "box", 0.9f, 30f, 30f, 50f, 50f, trackId = 3)
        tracker.reset(FrameDetections(100, 80, listOf(box)), patterned(0))
        var left = box.left
        for (i in 1..30) {
            val held = tracker.advance(patterned(0).copy(sequence = i.toLong(), capturedAtMs = i * 33L))
            assertNotNull(held)
            assertEquals(3, held!!.boxes.single().trackId)
            left = held.boxes.single().left
        }
        assertEquals(30f, left, 0.01f)
    }

    @Test fun hidesAJumpTheSearchCannotFollowAndDoesNotInventABox() {
        val tracker = MotionTracker()
        tracker.reset(FrameDetections(100, 80, listOf(
            BoxObservation("object", "box", 0.9f, 30f, 30f, 50f, 50f, trackId = 4)
        )), patterned(0))
        val lost = tracker.step(patterned(50).copy(sequence = 2, capturedAtMs = 33), nowMs = 33)
        assertTrue(lost.lost)
        assertNotNull(lost.frame)
        assertTrue(lost.frame!!.boxes.isEmpty())
        val stillHidden = tracker.step(patterned(50).copy(sequence = 3, capturedAtMs = 66), nowMs = 66)
        assertFalse(stillHidden.reacquired)
        assertTrue(stillHidden.frame == null || stillHidden.frame!!.boxes.isEmpty())
    }

    @Test fun reacquiresTheSameIdWithin250ms() {
        val tracker = MotionTracker()
        val box = BoxObservation("object", "box", 0.9f, 30f, 30f, 50f, 50f, trackId = 7)
        tracker.reset(FrameDetections(100, 80, listOf(box)), patterned(0))
        // 24 px is past the normal 16 px search plus its refine, and inside the 28 px reacquire window.
        val lost = tracker.step(patterned(24).copy(sequence = 2, capturedAtMs = 100), nowMs = 100)
        assertTrue(lost.lost)
        assertTrue(lost.frame!!.boxes.isEmpty())
        val back = tracker.step(patterned(24).copy(sequence = 3, capturedAtMs = 133), nowMs = 133)
        assertTrue(back.reacquired)
        assertTrue(back.reacquireMs in 0..250)
        assertEquals(7, back.frame!!.boxes.single().trackId)
        assertEquals(54f, back.frame.boxes.single().left, 1.5f)
    }

    @Test fun doesNotReacquireAfterTheWindow() {
        val tracker = MotionTracker()
        tracker.reset(FrameDetections(100, 80, listOf(
            BoxObservation("object", "box", 0.9f, 30f, 30f, 50f, 50f, trackId = 7)
        )), patterned(0))
        tracker.step(patterned(24).copy(sequence = 2, capturedAtMs = 100), nowMs = 100)
        val late = tracker.step(patterned(24).copy(sequence = 3, capturedAtMs = 400), nowMs = 400)
        assertFalse(late.reacquired)
        assertTrue(late.frame == null || late.frame!!.boxes.isEmpty())
    }

    @Test fun onePixelNoiseIsNotMotionAndATwoPixelShiftIs() {
        assertFalse(suppressStationaryJitter(0, 0, 10, 10))
        assertTrue(suppressStationaryJitter(1, 0, 20, 18))
        assertFalse(suppressStationaryJitter(1, 0, 80, 10))
        assertFalse(suppressStationaryJitter(2, 0, 100, 10))
    }
}
