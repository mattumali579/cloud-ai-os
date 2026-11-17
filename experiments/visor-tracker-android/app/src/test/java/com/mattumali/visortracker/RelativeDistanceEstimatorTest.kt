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

    @Test fun ignoresUnreliableTinyBoundingBox() {
        val estimator = RelativeDistanceEstimator()
        assertTrue(estimator.calibrate(frame(box(height = 10f)), 6f)
            .contains("Target too small"))
        assertNull(estimator.estimate(300, box()))
    }
}
