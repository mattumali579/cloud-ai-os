package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class StageFaultsTest {
    @Test fun videoSuccessDoesNotEraseADetectorFault() {
        val faults = StageFaults()
        faults.fail("detect", "model hung")
        faults.clear("video")
        assertEquals("detect: model hung", faults.summary())
    }

    @Test fun aFinishedStageClearsOnlyItself() {
        val faults = StageFaults()
        faults.fail("gray", "capture failed")
        faults.fail("detect", "model hung")
        faults.clear("gray")
        assertTrue(faults.summary().contains("detect"))
        assertTrue(!faults.summary().contains("gray"))
    }
}
