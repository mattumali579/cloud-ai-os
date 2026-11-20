package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TrainingHudTest {
    @Test fun pointingHandLocksTheObjectOnTheAimRayAsCup766() {
        val cup = BoxObservation("object", "cup", 0.97f, 120f, 30f, 170f, 70f, trackId = 766)
        val frame = FrameDetections(200, 200, listOf(cup, pointingHand(90f, 50f)))
        val status = TrainingHud.status(frame, lockedId = null)
        assertTrue(status.pointing)
        assertEquals(TrainingHud.POINTING, status.gesture)
        assertEquals(21, status.landmarks)
        assertEquals("cup", status.targetLabel)
        assertEquals(766, status.targetId)
        val lines = TrainingHud.lines(status, decoderFps = 30f)
        assertTrue(lines.any { it == "State: pointing hand" })
        assertTrue(lines.any { it == "Target: cup #766" })
        assertTrue(lines.any { it == "FPS: 30" })
    }

    @Test fun closedFistIsHandWithNoTarget() {
        val cup = BoxObservation("object", "cup", 0.97f, 120f, 30f, 170f, 70f, trackId = 766)
        val frame = FrameDetections(200, 200, listOf(cup, closedFist()))
        val status = TrainingHud.status(frame, lockedId = 766)
        assertFalse(status.pointing)
        assertEquals(TrainingHud.HAND, status.gesture)
        assertEquals(21, status.landmarks)
        assertNull(status.targetLabel)
        assertNull(status.targetId)
    }

    @Test fun hudPrefersThePointingHandOverAHigherScoreGrip() {
        val grip = closedFist().copy(score = 0.99f, trackId = 1)
        val pointer = pointingHand(90f, 50f).copy(score = 0.55f, trackId = 2)
        val cup = BoxObservation("object", "cup", 0.9f, 120f, 30f, 170f, 70f, trackId = 766)
        val status = TrainingHud.status(FrameDetections(200, 200, listOf(grip, pointer, cup)), lockedId = null)
        assertTrue(status.pointing)
        assertEquals(TrainingHud.POINTING, status.gesture)
        assertEquals(766, status.targetId)
        assertEquals(0.55f, status.score!!, 0.01f)
    }

    @Test fun pointingAtEmptySpaceHasNoObjectTarget() {
        val frame = FrameDetections(200, 200, listOf(pointingHand(90f, 50f)))
        val status = TrainingHud.status(frame, lockedId = null)
        assertTrue(status.pointing)
        assertNull(status.targetLabel)
        assertNull(status.targetId)
        assertTrue(TrainingHud.lines(status).any { it == "Target: none" })
    }
}
