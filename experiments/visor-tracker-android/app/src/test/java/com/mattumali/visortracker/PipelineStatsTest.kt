package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PipelineStatsTest {
    @Test fun percentile95UsesNearestRank() {
        assertEquals(0, PipelineStats.percentile95(emptyList()))
        assertEquals(95, PipelineStats.percentile95((1..100).toList()))
        assertEquals(7, PipelineStats.percentile95(listOf(7)))
    }

    @Test fun snapshotReportsAgeAndDiscardsThenResets() {
        val stats = PipelineStats()
        repeat(20) { stats.recordAge(it * 10) }
        stats.recordDiscard(2)
        stats.recordDiscard()
        val first = stats.snapshotAndReset()
        assertEquals(180, first.p95AgeMs)
        assertEquals(3, first.discarded)
        val cleared = stats.snapshotAndReset()
        assertEquals(0, cleared.p95AgeMs)
        assertEquals(0, cleared.discarded)
    }

    @Test fun reportNamesFreshRatesAgeAndDrops() {
        assertEquals(
            "V30 AI8 box20 draw30 age140ms p95220ms drop4 5obj",
            PipelineStats.formatReport(30f, 8f, 20f, 30f, 140, 220, 4, 5)
        )
        assertEquals(
            "V30 AI8 box20 draw30 age140ms p95220ms drop4 5obj g1 y8 ai40 m3 d90ms",
            PipelineStats.formatReport(30f, 8f, 20f, 30f, 140, 220, 4, 5, "g1 y8 ai40 m3 d90ms")
        )
    }
}

class StageTimingTest {
    @Test fun medianUsesTheMiddleSample() {
        val stages = StageTimings()
        stages.record("ai", 10)
        stages.record("ai", 40)
        stages.record("ai", 20)
        assertEquals(20, stages.median("ai"))
        assertEquals(0, stages.median("missing"))
    }
}

class FrameBudgetTest {
    @Test fun coalesceKeepsTheNewestFramesAndCountsTheDrop() {
        val frames = (1..10).map { GrayFrame(2, 2, byteArrayOf(1, 1, 1, 1), sequence = it.toLong()) }
        val (kept, dropped) = FrameBudget.coalesceNewest(frames, FrameBudget.MAX_GRAY_QUEUE)
        assertEquals(4, dropped)
        assertEquals(listOf(5L, 6L, 7L, 8L, 9L, 10L), kept.map { it.sequence })
    }

    @Test fun staleDetectorResultIsDroppedOnlyWhenItCannotBeRebased() {
        assertTrue(FrameBudget.shouldDropStaleDetection(canRebase = false, ageMs = 301))
        assertFalse(FrameBudget.shouldDropStaleDetection(canRebase = true, ageMs = 800))
        assertFalse(FrameBudget.shouldDropStaleDetection(canRebase = false, ageMs = 120))
    }
}
