package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
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
        val stillFirst = aim.observe(frame(box(1, 0f), box(2, 40f)))
        assertEquals(1, stillFirst.lockedId)
        assertFalse(stillFirst.changed)
    }

    @Test fun knownReplacementConfirmsOnTheSameDetection() {
        val aim = AimLock()
        val both = frame(box(1, 40f), box(2, 0f))
        aim.observe(both)
        assertEquals(1, aim.observe(both).lockedId)
        val gone = frame(box(2, 0f))
        assertTrue(KnownReplacement.alreadyVisible(both, gone, 1))
        val started = System.nanoTime()
        val switched = aim.observeKnownReplacement(gone)
        val elapsedMs = (System.nanoTime() - started) / 1_000_000
        assertEquals(2, switched.lockedId)
        assertTrue(switched.changed)
        assertFalse(switched.released)
        assertTrue(elapsedMs < 50)
        assertFalse(aim.sentExternalCommand)
    }

    @Test fun oneObservationStillWaitsWhenTheCallerDoesNotReuseTheFrame() {
        val aim = AimLock()
        val both = frame(box(1, 40f), box(2, 0f))
        aim.observe(both)
        aim.observe(both)
        val once = aim.observe(frame(box(2, 0f)))
        assertEquals(1, once.lockedId)
        assertFalse(once.changed)
    }

    @Test fun knownReplacementDoesNotSkipTheEmptyOcclusionHold() {
        val aim = AimLock()
        val held = frame(box(1, 40f))
        aim.observe(held)
        aim.observe(held)
        assertEquals(1, aim.observeKnownReplacement(frame()).lockedId)
        assertEquals(1, aim.observeKnownReplacement(frame()).lockedId)
        assertNull(aim.observeKnownReplacement(frame()).lockedId)
    }

    @Test fun oneFrameDoesNotSwitch() {
        val aim = AimLock()
        aim.observe(frame(box(1, 40f)))
        aim.observe(frame(box(1, 40f)))
        val once = aim.observe(frame(box(2, 40f)))
        assertEquals(1, once.lockedId)
        assertFalse(once.changed)
    }

    @Test fun selectsThePointedObjectAmongSeveralCandidates() {
        val aim = AimLock()
        val pointed = BoxObservation("object", "table", 0.9f, 10f, 20f, 60f, 90f, trackId = 7)
        val beside = BoxObservation("object", "cup", 0.9f, 55f, 42f, 70f, 58f, trackId = 4)
        val hand = BoxObservation("hand", "hand", 1f, 0f, 0f, 100f, 100f, trackId = 50)
        val picture = frame(beside, box(1, 0f), hand, box(3, 78f), pointed)
        assertFalse(aim.observe(picture).changed)
        val locked = aim.observe(picture)
        assertTrue(locked.changed)
        assertEquals(7, locked.lockedId)
    }

    @Test fun keepsTheLockWhileThePointStaysOnThatObject() {
        val aim = AimLock()
        val target = BoxObservation("object", "book", 0.9f, 10f, 20f, 70f, 90f, trackId = 2)
        val start = frame(box(1, 0f), target)
        aim.observe(start)
        assertEquals(2, aim.observe(start).lockedId)
        val intruder = BoxObservation("object", "book", 0.9f, 30f, 30f, 80f, 80f, trackId = 8)
        val held = frame(intruder, box(3, 78f), target)
        repeat(6) { index ->
            val decision = aim.observe(held)
            assertEquals("frame $index", 2, decision.lockedId)
            assertFalse(decision.changed)
        }
    }

    @Test fun replacesTheLockWhenThePointMovesToADifferentObject() {
        val aim = AimLock()
        val first = BoxObservation("object", "book", 0.9f, 30f, 30f, 70f, 80f, trackId = 1)
        val start = frame(box(3, 0f), first, box(4, 78f))
        aim.observe(start)
        assertEquals(1, aim.observe(start).lockedId)
        val aside = first.copy(left = 0f, right = 24f)
        val next = BoxObservation("object", "cup", 0.9f, 40f, 36f, 64f, 78f, trackId = 2)
        val moved = frame(box(3, 0f), aside, box(4, 78f), next)
        val hold = aim.observe(moved)
        assertEquals(1, hold.lockedId)
        assertFalse(hold.changed)
        val stayed = aim.observe(moved)
        assertEquals(1, stayed.lockedId)
        assertFalse(stayed.changed)
    }

    @Test fun aSmallerObjectUnderThePointReplacesTheLock() {
        val aim = AimLock()
        val table = BoxObservation("object", "table", 0.9f, 0f, 0f, 100f, 100f, trackId = 1)
        aim.observe(frame(table))
        assertEquals(1, aim.observe(frame(table)).lockedId)
        val cup = BoxObservation("object", "cup", 0.9f, 42f, 42f, 58f, 58f, trackId = 2)
        val picture = frame(table, cup)
        assertEquals(1, aim.observe(picture).lockedId)
        val stayed = aim.observe(picture)
        assertEquals(1, stayed.lockedId)
        assertFalse(stayed.changed)
    }

    @Test fun fingerRayLocksThePointedObjectAndIgnoresTheHandAfterItDrops() {
        val aim = AimLock()
        val pointed = BoxObservation("object", "cup", 0.8f, 120f, 30f, 170f, 70f, trackId = 2)
        val center = BoxObservation("object", "book", 0.95f, 70f, 70f, 130f, 130f, trackId = 1)
        val picture = frame(center, pointed, pointingHand(90f, 50f))
        assertEquals(null, aim.observe(picture).lockedId)
        val locked = aim.observe(picture)
        assertEquals(2, locked.lockedId)
        assertTrue(aim.fingerLocked)
        val handDown = frame(center, pointed)
        repeat(4) { assertEquals(2, aim.observe(handDown).lockedId) }
        assertTrue(aim.fingerLocked)
    }

    @Test fun lostPointedObjectIsReplacedByTheOtherVisibleObject() {
        val aim = AimLock()
        val pointed = BoxObservation("object", "cup", 0.8f, 120f, 30f, 170f, 70f, trackId = 2)
        val other = BoxObservation("object", "book", 0.7f, 10f, 10f, 40f, 40f, trackId = 7)
        val picture = frame(other, pointed, pointingHand(90f, 50f))
        aim.observe(picture)
        assertEquals(2, aim.observe(picture).lockedId)
        val gone = frame(other)
        assertEquals(2, aim.observe(gone).lockedId)
        val acquired = aim.observe(gone)
        assertEquals(7, acquired.lockedId)
        assertTrue(acquired.changed)
        assertEquals(7, aim.observe(gone).lockedId)
    }

    @Test fun oneRayFrameDoesNotStealAFingerLock() {
        val aim = AimLock()
        val first = BoxObservation("object", "cup", 0.8f, 120f, 30f, 170f, 70f, trackId = 2)
        val start = frame(first, pointingHand(90f, 50f))
        aim.observe(start)
        assertEquals(2, aim.observe(start).lockedId)
        val other = BoxObservation("object", "book", 0.9f, 120f, 120f, 170f, 160f, trackId = 4)
        val moved = frame(first, other, pointingHand(90f, 140f))
        assertEquals(2, aim.observe(moved).lockedId)
    }
}
