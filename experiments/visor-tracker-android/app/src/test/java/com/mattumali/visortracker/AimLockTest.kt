package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AimLockTest {
    private fun box(id: Int, x: Float) =
        BoxObservation("object", "chair", 0.4f, x, 40f, x + 20f, 80f, trackId = id)

    private fun frame(vararg boxes: BoxObservation) =
        FrameDetections(100, 100, boxes.toList())

    @Test fun twoDetectorFramesSwitchWhileBothStayVisible() {
        val aim = AimLock()
        val first = aim.observe(frame(box(1, 40f), box(2, 0f)))
        assertFalse(first.changed)
        val locked = aim.observe(frame(box(1, 40f), box(2, 0f)))
        assertTrue(locked.changed)
        assertEquals(1, locked.lockedId)
        val towardTwo = aim.observe(frame(box(1, 0f), box(2, 40f)))
        assertFalse(towardTwo.changed)
        assertEquals(1, towardTwo.lockedId)
        val switched = aim.observe(frame(box(1, 0f), box(2, 40f)))
        assertTrue(switched.changed)
        assertEquals(2, switched.lockedId)
        val backOne = aim.observe(frame(box(1, 40f), box(2, 0f)))
        assertFalse(backOne.changed)
        val back = aim.observe(frame(box(1, 40f), box(2, 0f)))
        assertEquals(1, back.lockedId)
    }

    @Test fun oneFrameDoesNotSwitch() {
        val aim = AimLock()
        aim.observe(frame(box(1, 40f)))
        aim.observe(frame(box(1, 40f)))
        val once = aim.observe(frame(box(2, 40f)))
        assertEquals(1, once.lockedId)
        assertFalse(once.changed)
    }
}
