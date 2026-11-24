package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Fixed frames through production tracker code. No sleep, no device, no emulator.
 */
class PipelineReplayTest {
    @Test fun mediaPipeIsNotClaimedAndVisorStaysUnverified() {
        val notes = PipelineReplay.boundaries()
        val mediaPipe = notes.single { it.component.contains("MediaPipe") }
        assertEquals(PipelineReplay.Kind.MOCKED, mediaPipe.kind)
        assertTrue(mediaPipe.detail.contains("not called"))
        val visor = notes.single { it.component.contains("Visor") }
        assertEquals(PipelineReplay.Kind.UNVERIFIED, visor.kind)
        val decoded = notes.single { it.component.contains("YuvSnapshot") }
        assertEquals(PipelineReplay.Kind.REAL, decoded.kind)
        val overlay = notes.single { it.component.contains("CoverGeometry") }
        assertEquals(PipelineReplay.Kind.REAL, overlay.kind)
    }

    @Test fun fixedYuvBecomesTheDetectorInput() {
        assertEquals(16 to 8, PipelineReplay.detectorInputSize())
    }

    @Test fun invalidYuvFramesFailInsteadOfDecoding() {
        val faults = PipelineReplay.invalidYuv().associate { it.scenario to it.message }
        for (scenario in listOf("zero-size", "empty", "short", "overflow", "use-after-return")) {
            assertTrue("$scenario decoded: ${faults[scenario]}", !faults[scenario].isNullOrBlank())
        }
    }

    @Test fun aTexturedObjectMovesAndABlankFramePublishesNothing() {
        val motion = PipelineReplay.moveThenDisappear()
        assertTrue(motion.movedLeft > motion.firstLeft)
        assertTrue(motion.movedProjectedLeft > motion.firstProjectedLeft)
        assertTrue(motion.movedVisible)
        // Aspect-fit preserves the camera margin instead of cover-cropping it.
        assertEquals(true, motion.marginVisible)
        assertTrue(motion.disappeared)
    }

    @Test fun aimDoesNotLockWhenNothingIsPointing() {
        val frames = PipelineReplay.switchTargets()
        assertTrue(frames.all { it.lockId == null })
    }

    @Test fun aMissingLockIsNotGivenToTheOtherObject() {
        val published = PipelineReplay.missingLockIsNotStolen()
        assertNotEquals(9, published)
    }

    @Test fun aHandFaultIsCopiedIntoTheNotification() {
        val line = PipelineReplay.handFaultNotification()
        assertTrue(line.contains("hands: model hung"))
        assertTrue(line.contains("V30"))
    }
}
