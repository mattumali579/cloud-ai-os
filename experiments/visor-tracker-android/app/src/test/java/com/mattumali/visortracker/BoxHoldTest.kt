package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BoxHoldTest {
    private val box = BoxObservation("object", "chair", 0.4f, 10f, 10f, 40f, 80f, trackId = 3)

    @Test fun emptyMotionDoesNotEraseAFreshBox() {
        val kept = BoxHold.keep(listOf(box), emptyList(), ageMs = 400)
        assertEquals(3, kept.single().trackId)
    }

    @Test fun aNewDetectionReplacesTheHold() {
        val next = box.copy(trackId = 9, left = 50f)
        val kept = BoxHold.keep(listOf(box), listOf(next), ageMs = 100)
        assertEquals(9, kept.single().trackId)
        assertTrue(kept.none { it.trackId == 3 })
    }

    @Test fun holdExpires() {
        assertTrue(BoxHold.keep(listOf(box), emptyList(), ageMs = BoxHold.HOLD_MS + 1).isEmpty())
    }
}
