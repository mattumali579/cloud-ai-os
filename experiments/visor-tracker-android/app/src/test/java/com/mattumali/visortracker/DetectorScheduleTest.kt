package com.mattumali.visortracker

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DetectorScheduleTest {
    @Test fun grayscaleIsNotRequiredToStartInference() {
        assertTrue(DetectorSchedule.shouldStart(inferenceDue = true, workerFree = true))
        assertFalse(DetectorSchedule.shouldStart(inferenceDue = false, workerFree = true))
        assertFalse(DetectorSchedule.shouldStart(inferenceDue = true, workerFree = false))
    }
}
