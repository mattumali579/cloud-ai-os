package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class GestureGateTest {
    @Test fun extendedIndexIsPointingAndSendsNothing() {
        val gate = GestureGate()
        val frame = FrameDetections(200, 200, listOf(pointingHand(90f, 50f)))
        val decision = gate.observe(frame, locked = true)
        assertEquals(HandGesture.POINTING, decision.gesture)
        assertFalse(decision.release)
        assertFalse(decision.sendsExternalCommand)
    }

    @Test fun missingHandIsAHoldAndDoesNotRelease() {
        val gate = GestureGate()
        val decision = gate.observe(FrameDetections(200, 200, emptyList()), locked = true)
        assertEquals(HandGesture.ABSENT, decision.gesture)
        assertFalse(decision.release)
        assertFalse(decision.sendsExternalCommand)
        val aim = AimLock()
        val target = objectBox(2)
        val picture = FrameDetections(200, 200, listOf(objectBox(1), target, pointingHand(90f, 50f)))
        aim.observe(picture)
        assertEquals(2, aim.observe(picture).lockedId)
        val dropped = FrameDetections(200, 200, listOf(objectBox(1), target))
        assertEquals(2, aim.observe(dropped).lockedId)
        assertTrue(aim.fingerLocked)
        assertFalse(aim.sentExternalCommand)
    }

    @Test fun oneClosedFrameDoesNotReleaseOrSwitch() {
        val aim = AimLock()
        lockPointed(aim)
        val once = aim.observe(fistFrame())
        assertEquals(2, once.lockedId)
        assertFalse(once.changed)
        assertFalse(once.released)
        assertFalse(aim.sentExternalCommand)
    }

    @Test fun curledIndexDoesNotSwitchAndOneFrameDoesNotRelease() {
        val aim = AimLock()
        lockPointed(aim)
        val curled = curledIndexHand()
        assertTrue(IndexPoint.curledIndex(curled))
        assertFalse(IndexPoint.fist(curled))
        assertNull(IndexPoint.extended(curled, 200, 200))
        val once = aim.observe(FrameDetections(200, 200, listOf(objectBox(1), objectBox(2), curled)))
        assertEquals(2, once.lockedId)
        assertFalse(once.released)
        assertFalse(aim.sentExternalCommand)
    }

    @Test fun stableClosedGestureReleasesWithoutACommand() {
        val aim = AimLock()
        lockPointed(aim)
        val fist = fistFrame()
        repeat(3) { index ->
            val held = aim.observe(fist)
            assertEquals("frame $index", 2, held.lockedId)
            assertFalse(held.released)
        }
        val released = aim.observe(fist)
        assertTrue(released.released)
        assertTrue(released.changed)
        assertNull(released.lockedId)
        assertFalse(aim.sentExternalCommand)
    }

    @Test fun releaseFramesOfOneStillSendsNoCommand() {
        val aim = AimLock(releaseFrames = 1)
        lockPointed(aim)
        val released = aim.observe(fistFrame())
        assertTrue(released.released)
        assertNull(released.lockedId)
        assertFalse(aim.sentExternalCommand)
    }

    private fun lockPointed(aim: AimLock) {
        val picture = FrameDetections(200, 200, listOf(objectBox(1), objectBox(2), pointingHand(90f, 50f)))
        aim.observe(picture)
        assertEquals(2, aim.observe(picture).lockedId)
        assertTrue(aim.fingerLocked)
    }

    private fun fistFrame() = FrameDetections(200, 200, listOf(objectBox(1), objectBox(2), closedFist()))

    private fun objectBox(id: Int) = when (id) {
        2 -> BoxObservation("object", "cup", 0.8f, 120f, 30f, 170f, 70f, trackId = 2)
        else -> BoxObservation("object", "book", 0.95f, 70f, 70f, 130f, 130f, trackId = id)
    }

    private fun curledIndexHand(): BoxObservation {
        val hand = pointingHand(90f, 50f)
        val points = hand.points.toMutableList()
        points[IndexPoint.INDEX_TIP] = points[IndexPoint.INDEX_MCP]
        points[12] = 140f to 40f
        return hand.copy(points = points)
    }
}
