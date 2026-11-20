package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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

    @Test fun anOptionalModelFailureDoesNotDiscardObjectBoxes() {
        var fault: String? = null
        val objects = listOf(BoxObservation("object", "chair", 0.8f, 1f, 1f, 20f, 30f))
        val kept = VisionPipeline.runOptional("hands", { fault = it }) { objects }
        assertNull(fault)
        assertEquals(objects, kept)
        val dropped = VisionPipeline.runOptional("hands", { fault = it }) {
            error("model hung")
        }
        assertTrue(dropped.isEmpty())
        assertEquals("hands: model hung", fault)
    }
}
