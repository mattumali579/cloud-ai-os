package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NearbyCandidatesTest {
    private fun box(
        id: Int,
        label: String,
        left: Float,
        top: Float,
        right: Float,
        bottom: Float,
        score: Float = 0.8f,
    ) = BoxObservation("object", label, score, left, top, right, bottom, trackId = id)

    @Test fun clutteredFrameDrawsTheCenterObjectOnly() {
        val selector = NearbyCandidates()
        val boxes = (1..8).map { box(it, "chair", it * 10f, 40f, it * 10f + 20f, 70f) }
        val center = box(9, "cup", 40f, 80f, 60f, 120f)
        val shown = selector.select(100, 200, boxes + center, lockedId = null)
        assertEquals(1, shown.size)
        assertEquals(9, shown.single().trackId)
        assertEquals(NearbyCandidates.DISPLAY_LIMIT, shown.size)
    }

    @Test fun confirmedLockStaysOneBoxWhileTheOtherObjectRemains() {
        val center = box(1, "chair", 40f, 80f, 60f, 120f)
        val other = box(2, "chair", 0f, 10f, 20f, 40f)
        val shown = NearbyCandidates().select(100, 200, listOf(center, other), lockedId = 2)
        assertEquals(1, shown.size)
        assertEquals(2, shown.single().trackId)
    }

    @Test fun captionNamesOneObject() {
        assertEquals("one object", NearbyCandidates.CAPTION)
    }

    @Test fun beforeLockALowerObjectDoesNotReplaceTheOneBeingPointedAt() {
        val selector = NearbyCandidates()
        val pointed = box(1, "book", 40f, 80f, 60f, 120f)
        val lower = box(2, "book", 0f, 160f, 30f, 190f)
        repeat(6) {
            val shown = selector.select(100, 200, listOf(pointed, lower), null)
            assertEquals(1, shown.single().trackId)
        }
    }

    @Test fun handsAreNotDrawn() {
        val selector = NearbyCandidates()
        val objectBox = box(1, "cup", 40f, 80f, 60f, 120f)
        val hand = BoxObservation("hand", "hand", 1f, 0f, 0f, 10f, 10f, trackId = 50)
        val shown = selector.select(100, 200, listOf(objectBox, hand), null)
        assertEquals(1, shown.size)
        assertEquals("object", shown.single().kind)
    }

    @Test fun lockedTrackIsTheOnlyBoxEvenInClutter() {
        val estimator = RelativeDistanceEstimator()
        val target = BoxObservation("object", "chair", 0.9f, 100f, 100f, 200f, 200f, trackId = 4)
        assertTrue(estimator.calibrate(FrameDetections(300, 300, listOf(target)), 6f).contains("Distance locked"))
        val clutter = (1..8).map {
            BoxObservation("object", "cup", 0.9f, 10f, 220f, 80f, 290f, trackId = 20 + it)
        }
        val shown = NearbyCandidates().select(300, 300, clutter + target, estimator.selectedTrackId())
        assertEquals(listOf(4), shown.map { it.trackId })
        assertEquals(6f, estimator.estimate(300, target)!!, 0.01f)
        assertNull(estimator.estimate(300, clutter.first()))
    }

    @Test fun missingLockStillDrawsTheCenterObject() {
        val selector = NearbyCandidates()
        val other = box(2, "cup", 40f, 80f, 60f, 120f)
        val shown = selector.select(100, 200, listOf(other), lockedId = 4)
        assertEquals(1, shown.size)
        assertEquals(2, shown.single().trackId)
    }
}
