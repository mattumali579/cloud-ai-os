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
        FrameDetections(220, 160, boxes.toList())

    private fun wide(vararg boxes: BoxObservation) = frame(*boxes)

    @Test fun twoDetectorFramesDoNotLockWithoutAPointingHand() {
        val aim = AimLock()
        val first = aim.observe(frame(box(1, 40f), box(2, 0f)))
        assertFalse(first.changed)
        assertNull(first.lockedId)
        val again = aim.observe(frame(box(1, 40f), box(2, 0f)))
        assertNull(again.lockedId)
        assertNull(aim.observe(frame(box(1, 0f), box(2, 40f))).lockedId)
    }

    @Test fun knownReplacementDoesNotInventALock() {
        val aim = AimLock()
        val both = frame(box(1, 40f), box(2, 0f))
        aim.observe(both)
        assertNull(aim.observe(both).lockedId)
        val gone = frame(box(2, 0f))
        assertTrue(KnownReplacement.alreadyVisible(both, gone, 1))
        val started = System.nanoTime()
        val switched = aim.observeKnownReplacement(gone)
        val elapsedMs = (System.nanoTime() - started) / 1_000_000
        assertNull(switched.lockedId)
        assertFalse(switched.changed)
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
        assertNull(once.lockedId)
        assertFalse(once.changed)
    }

    @Test fun anEmptyFrameDoesNotAcquireFromThePreviousCenter() {
        val aim = AimLock()
        val held = frame(box(1, 40f))
        aim.observe(held)
        aim.observe(held)
        assertNull(aim.observeKnownReplacement(frame()).lockedId)
        assertNull(aim.observeKnownReplacement(frame()).lockedId)
    }

    @Test fun oneFrameDoesNotSwitch() {
        val aim = AimLock()
        aim.observe(frame(box(1, 40f)))
        aim.observe(frame(box(1, 40f)))
        val once = aim.observe(frame(box(2, 40f)))
        assertNull(once.lockedId)
        assertFalse(once.changed)
    }

    @Test fun anArmBoxGuessDoesNotAcquireOrStealALock() {
        val phone = BoxObservation("object", "book", 0.9f, 60f, 20f, 95f, 50f, trackId = 4)
        val guess = HandTrack.armMarker(
            BoxObservation("object", "person", 0.8f, 0f, 10f, 100f, 80f, trackId = 8),
        )
        val fresh = AimLock()
        fresh.observe(frame(phone, guess))
        assertNull(fresh.observe(frame(phone, guess)).lockedId)
        val aim = AimLock()
        val cup = BoxObservation("object", "cup", 0.9f, 70f, 30f, 120f, 80f, trackId = 2)
        val measured = pointingHand(90f, 50f)
        val wide = { boxes: List<BoxObservation> -> FrameDetections(220, 160, boxes) }
        aim.observe(wide(listOf(cup, phone, measured)))
        assertEquals(2, aim.observe(wide(listOf(cup, phone, measured))).lockedId)
        assertNull(aim.observe(wide(listOf(cup, phone, guess))).lockedId)
    }

    @Test fun aLiveHandDoesNotLockTheScreenCenterWhenTheRayMisses() {
        val aim = AimLock()
        val center = BoxObservation("object", "book", 0.95f, 40f, 40f, 60f, 60f, trackId = 1)
        val picture = frame(center, pointingHand(90f, 50f))
        aim.observe(picture)
        assertNull(aim.observe(picture).lockedId)
    }

    @Test fun selectsThePointedObjectAmongSeveralCandidates() {
        val aim = AimLock()
        val pointed = BoxObservation("object", "book", 0.9f, 90f, 20f, 150f, 90f, trackId = 7)
        val beside = BoxObservation("object", "cup", 0.9f, 160f, 40f, 200f, 70f, trackId = 4)
        val picture = wide(beside, box(1, 0f), pointingHand(110f, 50f), box(3, 180f), pointed)
        assertFalse(aim.observe(picture).changed)
        val locked = aim.observe(picture)
        assertTrue(locked.changed)
        assertEquals(7, locked.lockedId)
    }

    @Test fun keepsTheLockWhileThePointStaysOnThatObject() {
        val aim = AimLock()
        val target = BoxObservation("object", "book", 0.9f, 90f, 20f, 150f, 90f, trackId = 2)
        val hand = pointingHand(110f, 50f)
        val start = wide(box(1, 0f), target, hand)
        aim.observe(start)
        assertEquals(2, aim.observe(start).lockedId)
        val intruder = BoxObservation("object", "book", 0.9f, 160f, 20f, 210f, 90f, trackId = 8)
        val held = wide(intruder, box(3, 180f), target, hand)
        repeat(6) { index ->
            val decision = aim.observe(held)
            assertEquals("frame $index", 2, decision.lockedId)
            assertFalse(decision.changed)
        }
    }

    @Test fun oneMovedPointDoesNotReplaceTheLock() {
        val aim = AimLock()
        val first = BoxObservation("object", "book", 0.9f, 90f, 20f, 150f, 90f, trackId = 1)
        val start = wide(box(3, 0f), first, pointingHand(110f, 50f))
        aim.observe(start)
        assertEquals(1, aim.observe(start).lockedId)
        val aside = first.copy(left = 0f, right = 40f)
        val next = BoxObservation("object", "cup", 0.9f, 150f, 20f, 200f, 80f, trackId = 2)
        val moved = wide(box(3, 0f), aside, next, pointingHand(160f, 50f))
        val hold = aim.observe(moved)
        assertEquals(1, hold.lockedId)
        assertFalse(hold.changed)
        val stayed = aim.observe(moved)
        assertEquals(1, stayed.lockedId)
        assertFalse(stayed.changed)
    }

    @Test fun aSmallerObjectWithoutAPointDoesNotLock() {
        val aim = AimLock()
        val table = BoxObservation("object", "book", 0.9f, 20f, 20f, 80f, 80f, trackId = 1)
        aim.observe(wide(table))
        assertNull(aim.observe(wide(table)).lockedId)
        val cup = BoxObservation("object", "cup", 0.9f, 42f, 42f, 58f, 58f, trackId = 2)
        assertNull(aim.observe(wide(table, cup)).lockedId)
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
        repeat(2) { assertEquals(2, aim.observe(handDown).lockedId) }
        assertTrue(aim.fingerLocked)
        assertNull(aim.observe(handDown).lockedId)
        assertFalse(aim.fingerLocked)
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
        assertEquals(2, aim.observe(gone).lockedId)
        val cleared = aim.observe(gone)
        assertNull(cleared.lockedId)
        assertTrue(cleared.changed)
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

    @Test fun vanishedLockedObjectClearsAfterLostFramesLimitWhileHandContinuesPointing() {
        val aim = AimLock(lostFramesLimit = 3)
        val target = BoxObservation("object", "cup", 0.9f, 120f, 30f, 170f, 70f, trackId = 2)
        val hand = pointingHand(90f, 50f)
        val picture = frame(target, hand)
        aim.observe(picture)
        val locked = aim.observe(picture)
        assertEquals(2, locked.lockedId)

        // Target vanishes from frame while pointing hand remains
        val vanished = frame(hand)
        val frame1 = aim.observe(vanished)
        assertEquals("frame 1 held", 2, frame1.lockedId)
        assertFalse(frame1.changed)

        val frame2 = aim.observe(vanished)
        assertEquals("frame 2 held", 2, frame2.lockedId)
        assertFalse(frame2.changed)

        val frame3 = aim.observe(vanished)
        assertNull("frame 3 cleared", frame3.lockedId)
        assertTrue(frame3.changed)
    }
}
