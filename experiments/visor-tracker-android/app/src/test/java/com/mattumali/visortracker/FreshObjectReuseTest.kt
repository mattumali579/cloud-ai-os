package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FreshObjectReuseTest {
    private fun box(id: Int) =
        BoxObservation("object", "person", 0.8f, 10f, 10f, 40f, 80f, trackId = id)

    @Test fun anotherVisibleObjectIsFreshInsideTheAgeLimit() {
        val previous = FrameDetections(100, 100, listOf(box(1), box(2)))
        assertTrue(FreshObjectReuse.replacementIsFresh(previous, lockedId = 1, ageMs = 0L))
        assertTrue(FreshObjectReuse.replacementIsFresh(previous, lockedId = 1, ageMs = FreshObjectReuse.MAX_AGE_MS))
    }

    @Test fun anEmptyOrStaleFrameIsNotAFreshReplacement() {
        val onlyLocked = FrameDetections(100, 100, listOf(box(1)))
        assertFalse(FreshObjectReuse.replacementIsFresh(onlyLocked, lockedId = 1, ageMs = 0L))
        assertFalse(FreshObjectReuse.replacementIsFresh(null, lockedId = 1, ageMs = 0L))
        val both = FrameDetections(100, 100, listOf(box(1), box(2)))
        assertFalse(FreshObjectReuse.replacementIsFresh(both, lockedId = 1, ageMs = FreshObjectReuse.MAX_AGE_MS + 1))
        assertFalse(FreshObjectReuse.replacementIsFresh(both, lockedId = 0, ageMs = 0L))
    }

    @Test fun oneFreshOcclusionSkipsTheObjectModelThenTheNextDuePassRunsIt() {
        assertFalse(ObjectSkip.runObjects(due = true, replacementFresh = true, obscured = true, alreadySkipped = false))
        assertTrue(ObjectSkip.runObjects(due = true, replacementFresh = true, obscured = true, alreadySkipped = true))
        assertTrue(ObjectSkip.runObjects(due = true, replacementFresh = false, obscured = true, alreadySkipped = false))
        assertFalse(ObjectSkip.runObjects(due = false, replacementFresh = false, obscured = false, alreadySkipped = false))
    }
}

class RegionChangeTest {
    @Test fun identicalSamplesAreNotObscured() {
        val pixels = IntArray(64) { 0xff112233.toInt() }
        assertFalse(RegionChange.obscured(pixels, pixels.copyOf()))
        assertEquals(0, RegionChange.channelDelta(0xff112233.toInt(), 0xff112233.toInt()))
    }

    @Test fun aFlatCoverIsObscuredAndSmallNoiseIsNot() {
        val before = IntArray(64) { 0xffc45a32.toInt() }
        val covered = IntArray(64) { 0xff444444.toInt() }
        assertTrue(RegionChange.obscured(before, covered))
        val noisy = before.copyOf()
        noisy[0] = 0xffc45a40.toInt()
        assertFalse(RegionChange.obscured(before, noisy))
        assertFalse(RegionChange.obscured(IntArray(0), IntArray(0)))
    }
}
