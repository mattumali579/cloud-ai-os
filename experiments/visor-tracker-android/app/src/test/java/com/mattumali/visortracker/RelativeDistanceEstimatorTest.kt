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
}
