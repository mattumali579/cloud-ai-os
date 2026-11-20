package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class GestureStateTest {
    @Test fun anOpenPalmIsNotAPointAndDoesNotLock() {
        val palm = pointingHand(90f, 50f).let { hand ->
            val points = hand.points.toMutableList()
            points[12] = 90f to 50f
            points[16] = 90f to 55f
            points[20] = 88f to 58f
            points[IndexPoint.INDEX_TIP] = points[IndexPoint.INDEX_MCP]
            hand.copy(points = points)
        }
        val cup = BoxObservation("object", "cup", 0.9f, 120f, 30f, 170f, 70f, trackId = 2)
        val frame = FrameDetections(220, 160, listOf(cup, palm))
        assertEquals(GestureState.HAND_VISIBLE, GestureStates.classify(frame))
        val aim = AimLock()
        aim.observe(frame)
        assertNull(aim.observe(frame).lockedId)
    }

    @Test fun anArmMarkerIsNotAConfirmedPoint() {
        val arm = HandTrack.armMarker(
            BoxObservation("object", "person", 0.9f, 500f, 900f, 1080f, 2200f, trackId = 140),
        )
        val frame = FrameDetections(1080, 2316, listOf(arm))
        assertEquals(GestureState.ARM_VISIBLE, GestureStates.classify(frame))
        assertEquals(0, arm.points.size)
        val aim = AimLock()
        assertNull(aim.observe(frame).lockedId)
    }

    @Test fun noHandDoesNotLockTheScreenCenter() {
        val center = BoxObservation("object", "book", 0.95f, 40f, 40f, 70f, 80f, trackId = 1)
        val frame = FrameDetections(100, 100, listOf(center))
        assertEquals(GestureState.NONE, GestureStates.classify(frame))
        val aim = AimLock()
        aim.observe(frame)
        assertNull(aim.observe(frame).lockedId)
    }

    @Test fun aConfirmedIndexLocksTheObjectOnTheRay() {
        val cup = BoxObservation("object", "cup", 0.9f, 120f, 30f, 170f, 70f, trackId = 2)
        val frame = FrameDetections(220, 160, listOf(cup, pointingHand(90f, 50f)))
        assertEquals(GestureState.POINTING_CONFIRMED, GestureStates.classify(frame))
        val aim = AimLock()
        aim.observe(frame)
        assertEquals(2, aim.observe(frame).lockedId)
    }
}
