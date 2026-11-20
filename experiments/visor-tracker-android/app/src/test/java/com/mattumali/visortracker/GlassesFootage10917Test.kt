package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 10917 showed a forearm where MediaPipe had no landmarks. That arm is not an
 * index finger and must not lock the barrel or the phone.
 */
class GlassesFootage10917Test {
    private val width = 1080
    private val height = 2316

    private fun barrel() = BoxObservation("object", "cup", 0.86f, 40f, 900f, 280f, 1300f, trackId = 112)
    private fun phone() = BoxObservation("object", "book", 0.9f, 620f, 1550f, 950f, 2100f, trackId = 168)
    private fun arm() = BoxObservation("object", "person", 0.88f, 500f, 900f, 1080f, 2200f, trackId = 140)

    @Test fun aWearerArmIsNotDrawnAsAPerson() {
        val visible = HandPriority.apply(listOf(arm(), barrel(), phone(), HandTrack.armMarker(arm())), width, height)
        assertFalse(visible.any { it.label == "person" })
        assertTrue(visible.any { it.kind == "arm" })
        assertTrue(visible.any { it.trackId == 112 })
        assertTrue(visible.any { it.trackId == 168 })
    }

    @Test fun aForearmDoesNotConfirmPointingOrLock() {
        val frame = FrameDetections(
            width,
            height,
            HandPriority.apply(listOf(arm(), barrel(), phone(), HandTrack.armMarker(arm())), width, height),
        )
        assertEquals(GestureState.ARM_VISIBLE, GestureStates.classify(frame))
        assertNull(IndexPoint.aimed(frame))
        val aim = AimLock()
        aim.observe(frame)
        assertNull(aim.observe(frame).lockedId)
    }
}
