package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Same boxes must produce the same gesture and lock. No second tracker. */
class ProductionTrackerTest {
    @Test fun anArmBoxDoesNotConfirmOrLock() {
        val tracker = ProductionTracker()
        val arm = BoxObservation("object", "person", 0.8f, 0f, 40f, 80f, 200f, trackId = 9)
        val cup = BoxObservation("object", "cup", 0.9f, 120f, 30f, 170f, 70f)
        val step = tracker.step(listOf(arm, cup), 220, 220)
        assertEquals(GestureState.ARM_VISIBLE, step.state)
        assertEquals(false, step.ray)
        assertNull(step.lockId)
        assertEquals(0, step.boxes.count { it.kind == "hand" })
        assertEquals(1, step.boxes.count { it.kind == "arm" })
    }

    @Test fun aConfirmedIndexLocksTheCupAndAFollowingArmClearsIt() {
        val tracker = ProductionTracker()
        val cup = BoxObservation("object", "cup", 0.9f, 120f, 30f, 170f, 70f)
        val hand = pointingHand(90f, 50f)
        val first = tracker.step(listOf(cup, hand), 220, 160)
        assertEquals(GestureState.POINTING_CONFIRMED, first.state)
        assertNull(first.lockId)
        var locked = first
        repeat(5) { locked = tracker.step(listOf(cup, hand), 220, 160) }
        val cupId = locked.boxes.first { it.label == "cup" }.trackId
        assertEquals(cupId, locked.lockId)
        assertEquals(true, locked.ray)
        val arm = BoxObservation("object", "person", 0.8f, 0f, 40f, 70f, 150f)
        var dropped = tracker.step(listOf(cup, arm), 220, 160)
        assertEquals(cupId, dropped.lockId)
        repeat(3) { dropped = tracker.step(listOf(cup, arm), 220, 160) }
        assertEquals(GestureState.ARM_VISIBLE, dropped.state)
        assertNull(dropped.lockId)
    }
}
