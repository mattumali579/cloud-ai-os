package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DetectorScheduleTest {
    @Test fun grayscaleIsNotRequiredToStartInference() {
        assertTrue(DetectorSchedule.shouldStart(inferenceDue = true, workerFree = true))
        assertFalse(DetectorSchedule.shouldStart(inferenceDue = false, workerFree = true))
        assertFalse(DetectorSchedule.shouldStart(inferenceDue = true, workerFree = false))
    }

    @Test fun lockedTrackRunsHandsBetweenObjectPasses() {
        var lastObject = 0L
        var lastHand = 0L
        var objectRuns = 0
        var handRuns = 0
        var handOnly = 0
        val start = 10_000L
        for (step in 0..30) {
            val now = start + step * 33L
            val pass = SplitSchedule.decide(now, lastObject, lastHand, locked = true) ?: continue
            if (pass.objects) {
                objectRuns += 1
                lastObject = now
            }
            if (pass.hands) {
                handRuns += 1
                lastHand = now
            }
            if (pass.hands && !pass.objects) handOnly += 1
        }
        assertEquals(4, objectRuns)
        assertEquals(16, handRuns)
        assertEquals(12, handOnly)
        assertTrue(InferenceCadence.intervalMs(locked = true) >= SplitSchedule.HAND_MS)
    }
}
