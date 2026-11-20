package com.mattumali.visortracker

import org.junit.Assert.*
import org.junit.Test

class RelativeDistanceEstimatorTest {
    private fun box(id: Int = 4, height: Float = 100f, x: Float = 100f) =
        BoxObservation("object", "chair", 0.91f, x, 100f, x + 100f, 100f + height, trackId = id)

    private fun frame(vararg boxes: BoxObservation) =
        FrameDetections(300, 300, boxes.toList())

    @Test fun calibratedSameObjectReadsReferenceDistance() {
        val estimator = RelativeDistanceEstimator()
        assertTrue(estimator.calibrate(frame(box()), 6f).contains("Distance locked"))
        assertEquals(6f, estimator.estimate(300, box())!!, 0.01f)
    }

    @Test fun doublingApparentHeightMakesDistanceHalfAfterSmoothing() {
        val estimator = RelativeDistanceEstimator()
        estimator.calibrate(frame(box()), 6f)
        // Apply repeated new observations so the smoothing converges.
        repeat(20) { estimator.estimate(300, box(height = 200f)) }
        assertEquals(3f, estimator.estimate(300, box(height = 200f))!!, 0.02f)
    }

    @Test fun neverReportsAnotherTargetDistance() {
        val estimator = RelativeDistanceEstimator()
        estimator.calibrate(frame(box()), 6f)
        assertNull(estimator.estimate(300, box(id = 99)))
        assertNull(estimator.estimate(300,
            BoxObservation("hand", "hand", 0.9f, 100f, 100f, 200f, 200f, trackId = 4)))
    }

    private fun at(id: Int, centerX: Float, height: Float = 100f) =
        box(id = id, height = height, x = centerX - 50f)

    @Test fun pointingAtCenterLocksThatTrack() {
        val estimator = RelativeDistanceEstimator()
        val target = at(4, 150f)
        val picture = frame(target, pointingHand(140f, 150f))
        assertNull(estimator.observe(picture))
        val locked = estimator.observe(picture)
        assertTrue(locked!!.contains("Locked #4"))
        assertEquals(4, estimator.selectedTrackId())
        assertTrue(estimator.calibrate(frame(target), 6f).contains("#4"))
        assertEquals(6f, estimator.estimate(300, target)!!, 0.01f)
    }

    @Test fun severalCandidatesLockThePointedObjectAndANewPointReplacesIt() {
        val estimator = RelativeDistanceEstimator()
        val pointed = at(4, 150f)
        val left = at(1, 40f)
        val right = at(2, 260f)
        val picture = frame(left, right, pointed, pointingHand(140f, 150f))
        assertNull(estimator.observe(picture))
        val locked = estimator.observe(picture)
        assertTrue(locked!!.contains("Locked #4"))
        assertEquals(4, estimator.selectedTrackId())
        assertTrue(estimator.calibrate(frame(left, right, pointed), 6f).contains("#4"))
        val sameSizeNeighbor = at(8, 150f)
        repeat(4) {
            assertNull(estimator.observe(frame(sameSizeNeighbor, pointed, left, pointingHand(140f, 150f))))
        }
        assertEquals(4, estimator.selectedTrackId())
        assertEquals(6f, estimator.estimate(300, pointed)!!, 0.05f)
        assertNull(estimator.estimate(300, sameSizeNeighbor))
        val aside = at(4, 40f)
        val next = at(8, 150f)
        repeat(6) {
            assertNull(estimator.observe(frame(aside, next, right, pointingHand(80f, 150f))))
        }
        assertEquals(4, estimator.selectedTrackId())
        assertEquals(6f, estimator.estimate(300, pointed)!!, 0.05f)
    }

    @Test fun holdingANewCenterTargetClearsTheOldDistance() {
        val estimator = RelativeDistanceEstimator()
        val first = at(4, 150f)
        repeat(3) { estimator.observe(frame(first)) }
        assertTrue(estimator.calibrate(frame(first), 6f).contains("6.0"))
        val next = at(8, 150f)
        val aside = at(4, 40f)
        repeat(6) { assertNull(estimator.observe(frame(next, aside))) }
        assertEquals(4, estimator.selectedTrackId())
        assertEquals(6f, estimator.estimate(300, first)!!, 0.01f)
    }

    @Test fun motionJitterDoesNotSwapTheLockEveryFrame() {
        val estimator = RelativeDistanceEstimator()
        val held = at(4, 160f)
        repeat(3) { estimator.observe(frame(held)) }
        estimator.calibrate(frame(held), 6f)
        val intruder = at(8, 150f)
        repeat(8) { index ->
            val picture = if (index % 2 == 0) frame(held, intruder) else frame(held, at(8, 20f))
            assertNull(estimator.observe(picture))
        }
        assertEquals(4, estimator.selectedTrackId())
        assertEquals(6f, estimator.estimate(300, held)!!, 0.05f)
        assertNull(estimator.estimate(300, intruder))
    }

    @Test fun noCalibrationForBadMeasurementsOrUncenteredObjects() {
        val estimator = RelativeDistanceEstimator()
        assertTrue(estimator.calibrate(frame(box()), 0f).contains("Enter"))
        assertNull(estimator.estimate(300, box()))
        assertTrue(estimator.calibrate(frame(box(x = 270f)), 6f).contains("Aim"))
    }

    @Test fun calibrationDoesNotPersistAfterClear() {
        val estimator = RelativeDistanceEstimator()
        estimator.calibrate(frame(box()), 6f)
        estimator.clear()
        assertNull(estimator.estimate(300, box()))
    }

    @Test fun labelChangeAndReorderDoNotMoveCalibrationOntoAnotherTrack() {
        val estimator = RelativeDistanceEstimator()
        val target = box()
        val other = box(id = 8, x = 20f)
        assertTrue(estimator.calibrate(frame(other, target), 6f).contains("Distance locked"))
        assertEquals(4, estimator.selectedTrackId())
        val relabeled = target.copy(label = "couch")
        assertEquals(6f, estimator.estimate(300, relabeled)!!, 0.01f)
        assertEquals(4, estimator.selectedTrackId())
        assertNull(estimator.estimate(300, other))
        assertEquals(DistanceReadiness.LIVE, estimator.readiness(listOf(other, relabeled)))
    }

    @Test fun missingTargetStaysStaleInsteadOfAdoptingANeighbor() {
        val estimator = RelativeDistanceEstimator()
        estimator.calibrate(frame(box()), 8f)
        assertEquals(DistanceReadiness.STALE, estimator.readiness(emptyList()))
        assertTrue(estimator.banner(emptyList()).contains("lock lost"))
        assertNull(estimator.estimate(300, box(id = 15, height = 180f)))
        assertEquals(4, estimator.selectedTrackId())
        assertEquals(DistanceReadiness.LIVE, estimator.readiness(listOf(box(height = 50f))))
        repeat(12) { estimator.estimate(300, box(height = 50f)) }
        assertEquals(16f, estimator.estimate(300, box(height = 50f))!!, 0.15f)
    }

    @Test fun ignoresUnreliableTinyBoundingBox() {
        val estimator = RelativeDistanceEstimator()
        assertTrue(estimator.calibrate(frame(box(height = 10f)), 6f)
            .contains("Target too small"))
        assertNull(estimator.estimate(300, box()))
    }

    @Test fun redrawOfTheSameFrameDoesNotMoveFeet() {
        val estimator = RelativeDistanceEstimator()
        estimator.calibrate(frame(box()), 6f)
        val first = estimator.estimate(300, box(height = 104f), sequence = 3)!!
        val redraw = estimator.estimate(300, box(height = 104f), sequence = 3)!!
        assertEquals(first, redraw, 0.0001f)
        val nextFrame = estimator.estimate(300, box(height = 104f), sequence = 4)!!
        val raw = DistanceScale.feet(6f, 100f, 104f)!!
        assertTrue(kotlin.math.abs(nextFrame - raw) < kotlin.math.abs(first - raw))
    }

    @Test fun oneFrameSizeChangeUpdatesFeetImmediately() {
        val estimator = RelativeDistanceEstimator()
        assertTrue(estimator.calibrate(frame(box()), 6f).contains("6.0"))
        assertEquals(3f, estimator.estimate(300, box(height = 200f))!!, 0.05f)
    }

    @Test fun knownThreeSixAndTenFootCasesAndTheTiltLimit() {
        val estimator = RelativeDistanceEstimator()
        val message = estimator.calibrate(frame(box(height = 120f)), 6f)
        assertTrue(message.contains("Distance locked"))
        assertTrue(message.contains("6.0"))
        // Similar triangles: height scales as 1/distance. 120 px at 6 ft.
        val at3 = estimator.estimate(300, box(height = 240f))!!
        val at10 = estimator.estimate(300, box(height = 72f))!!
        val at6 = estimator.estimate(300, box(height = 120f))!!
        val err3 = DistanceScale.percentError(at3, 3f)
        val err10 = DistanceScale.percentError(at10, 10f)
        val err6 = DistanceScale.percentError(at6, 6f)
        println("similar-triangles percent error: 3ft=$err3 6ft=$err6 10ft=$err10")
        assertTrue("3 ft error $err3%", err3 < 5f)
        assertTrue("10 ft error $err10%", err10 < 5f)
        assertTrue("6 ft error $err6%", err6 < 5f)
        assertNull(estimator.estimate(300, box(id = 99, height = 72f)))
        assertEquals(DistanceReadiness.STALE, estimator.readiness(listOf(box(id = 99, height = 72f))))

        // 8% box-height noise at the 10 ft pose stays inside 25%.
        val noisy = DistanceScale.feet(6f, 120f, 72f * 1.08f)!!
        val noisyErr = DistanceScale.percentError(noisy, 10f)
        assertTrue("noise error $noisyErr%", noisyErr < 25f)

        // A 35% shorter box from tilt is not a distance change. The formula
        // still emits a number, and that number is not within 25%.
        val tilted = DistanceScale.feet(6f, 120f, 72f * 0.65f)!!
        val tiltErr = DistanceScale.percentError(tilted, 10f)
        println("height-noise percent error at 10ft=$noisyErr tilt percent error=$tiltErr")
        assertTrue("tilt error $tiltErr% must not be called accurate", tiltErr > 25f)
        assertEquals("distance unavailable", estimator.shortStatus(emptyList()))
        assertTrue(estimator.banner(listOf(box(height = 120f))).contains("6.0"))
    }

    @Test fun aVisibleNeighborDoesNotReplaceALostPoint() {
        val estimator = RelativeDistanceEstimator()
        val locked = at(4, 150f)
        val other = at(2, 260f)
        val picture = frame(locked, other, pointingHand(140f, 150f))
        assertNull(estimator.observe(picture))
        assertTrue(estimator.observe(picture)!!.contains("Locked #4"))
        assertNull(estimator.observe(frame(other)))
        assertEquals(4, estimator.selectedTrackId())
        assertNull(estimator.observe(frame(other)))
        val released = estimator.observe(frame(other))
        assertTrue(released!!.contains("Lock released"))
        assertNull(estimator.selectedTrackId())
    }

    @Test fun anUnseenNeighborDoesNotBecomeTheLock() {
        val estimator = RelativeDistanceEstimator()
        val locked = at(4, 150f)
        val other = at(2, 260f)
        val picture = frame(locked, pointingHand(140f, 150f))
        assertNull(estimator.observe(picture))
        assertNotNull(estimator.observe(picture))
        assertNull(estimator.observe(frame(other)))
        assertEquals(4, estimator.selectedTrackId())
        assertNull(estimator.observe(frame(other)))
        assertEquals(4, estimator.selectedTrackId())
    }
}
