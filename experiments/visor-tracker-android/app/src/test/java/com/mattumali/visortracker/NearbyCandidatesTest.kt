package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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

    @Test fun clutteredFrameNeverDrawsMoreThanFive() {
        val selector = NearbyCandidates()
        val boxes = (1..8).map { box(it, "chair", 0f, it * 10f, 40f, it * 10f + 30f) }
        val shown = selector.select(100, 200, boxes, lockedId = null)
        assertTrue(shown.size <= NearbyCandidates.DISPLAY_LIMIT)
        assertEquals(5, shown.size)
        assertTrue(shown.all { it.kind == "object" })
    }

    @Test fun captionNamesTheVisualEstimateAndNotMeasuredDistance() {
        assertEquals("up to 5 visual candidates", NearbyCandidates.CAPTION)
    }

    @Test fun lowerSameClassObjectBeatsOneNearTheFrameCenter() {
        val selector = NearbyCandidates()
        val centeredHigh = box(1, "book", 40f, 10f, 60f, 30f)
        val lower = box(2, "book", 0f, 160f, 30f, 190f)
        val shown = selector.select(100, 200, listOf(centeredHigh, lower), null)
        assertEquals(2, shown.first().trackId)
    }

    @Test fun aHugeHighObjectDoesNotWinOnAreaAcrossClasses() {
        val selector = NearbyCandidates()
        val bus = box(1, "bus", 0f, 0f, 90f, 40f)
        val cup = box(2, "cup", 10f, 150f, 30f, 190f)
        val shown = selector.select(100, 200, listOf(bus, cup), null)
        assertEquals(2, shown.first().trackId)
    }

    @Test fun oneFrameSpikeDoesNotSwapTheFive() {
        val selector = NearbyCandidates()
        val stable = (1..5).map { box(it, "chair", 0f, 140f, 20f, 180f) }
        repeat(3) { selector.select(100, 200, stable, null) }
        val challenger = box(99, "chair", 0f, 150f, 25f, 195f)
        val once = selector.select(100, 200, stable + challenger, null)
        assertFalse(once.any { it.trackId == 99 })
        var last = once
        repeat(3) { last = selector.select(100, 200, stable + challenger, null) }
        assertTrue(last.any { it.trackId == 99 })
        assertTrue(last.size <= 5)
    }

    @Test fun handsDoNotConsumeObjectSlots() {
        val selector = NearbyCandidates()
        val objects = (1..5).map { box(it, "cup", 0f, 150f, 20f, 180f) }
        val hand = BoxObservation("hand", "hand", 1f, 0f, 0f, 10f, 10f, trackId = 50)
        val shown = selector.select(100, 200, objects + hand, null)
        assertEquals(5, shown.size)
        assertTrue(shown.all { it.kind == "object" })
    }

    @Test fun handsFillOnlyLeftoverSlots() {
        val selector = NearbyCandidates()
        val objects = (1..4).map { box(it, "cup", 0f, 150f, 20f, 180f) }
        val hand = BoxObservation("hand", "hand", 1f, 0f, 0f, 10f, 10f, trackId = 50)
        val shown = selector.select(100, 200, objects + hand, null)
        assertEquals(5, shown.size)
        assertEquals(1, shown.count { it.kind == "hand" })
    }

    @Test fun calibratedTrackStaysInsideFiveAndKeepsOnlyItsDistance() {
        val estimator = RelativeDistanceEstimator()
        val target = BoxObservation("object", "chair", 0.9f, 100f, 100f, 200f, 200f, trackId = 4)
        assertTrue(estimator.calibrate(FrameDetections(300, 300, listOf(target)), 6f).contains("Distance locked"))
        val clutter = (1..8).map {
            BoxObservation("object", "cup", 0.9f, 10f, 220f, 80f, 290f, trackId = 20 + it)
        }
        val shown = NearbyCandidates().select(300, 300, clutter + target, estimator.selectedTrackId())
        assertTrue(shown.any { it.trackId == 4 })
        assertTrue(shown.size <= 5)
        assertEquals(6f, estimator.estimate(300, target)!!, 0.01f)
        assertNull(estimator.estimate(300, shown.first { it.trackId != 4 }))
        assertEquals(DistanceReadiness.LIVE, estimator.readiness(shown))
    }

    @Test fun weakUncertainNamesDoNotFillTheFive() {
        val selector = NearbyCandidates()
        val center = box(1, "object", 40f, 80f, 60f, 120f, score = 0.50f)
        val weak = (2..6).map { box(it, "object", 0f, 10f, 20f, 30f, score = 0.50f) }
        val chair = box(7, "chair", 0f, 160f, 40f, 190f, score = 0.9f)
        val shown = selector.select(100, 200, weak + center + chair, lockedId = null)
        assertTrue(shown.any { it.trackId == 7 })
        assertTrue(shown.any { it.trackId == 1 })
        assertTrue(shown.none { it.trackId in 2..6 })
        assertTrue(shown.size < NearbyCandidates.DISPLAY_LIMIT)
    }

    @Test fun lockedUncertainTrackStaysEvenWhenOffCenter() {
        val selector = NearbyCandidates()
        val locked = box(4, "object", 0f, 10f, 20f, 40f, score = 0.50f)
        val chairs = (1..5).map { box(10 + it, "chair", 0f, 150f, 30f, 190f, score = 0.9f) }
        val shown = selector.select(100, 200, chairs + locked, lockedId = 4)
        assertTrue(shown.any { it.trackId == 4 })
        assertTrue(shown.size <= 5)
    }
}
