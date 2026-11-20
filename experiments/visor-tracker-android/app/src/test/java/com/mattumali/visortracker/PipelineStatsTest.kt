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

    @Test fun percentile50And99UseNearestRank() {
        assertEquals(50, PipelineStats.percentile((1..100).toList(), 0.50))
        assertEquals(99, PipelineStats.percentile((1..100).toList(), 0.99))
    }

    @Test fun reportNamesTrackerIntervalsAgeAndDrops() {
        val lock = LockSnapshot(
            decoded = 30,
            processed = 29,
            intervalP50 = 33,
            intervalP95 = 40,
            intervalP99 = 48,
            ageP50 = 8,
            ageP95 = 18,
            ageP99 = 40,
            dropped = 1,
            staleDetections = 0,
            jitterP95Fraction = 0.004f,
            idSwitches = 0,
            lockLosses = 0,
            reacquireP95Ms = -1,
        )
        assertEquals(
            "V30 AI4 trk29 box2 draw30 i33/40/48 a8/18/40 drop1 stale0 jit0.40% id0 loss0 ra- 1obj",
            PipelineStats.formatReport(30f, 4f, 29f, 2f, 30f, lock, 1)
        )
        assertEquals(
            "V30 AI4 trk29 box2 draw30 i33/40/48 a8/18/40 drop1 stale0 jit0.40% id0 loss0 ra- 1obj g1 y8 ai40 m3 d90ms",
            PipelineStats.formatReport(30f, 4f, 29f, 2f, 30f, lock, 1, "g1 y8 ai40 m3 d90ms")
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

    @Test fun anchorOutsideTheRebaseHistoryCannotBeWalked() {
        assertTrue(FrameBudget.anchorStillInHistory(5, oldestSequence = 5))
        assertTrue(FrameBudget.anchorStillInHistory(6, oldestSequence = 5))
        assertFalse(FrameBudget.anchorStillInHistory(4, oldestSequence = 5))
        assertTrue(FrameBudget.anchorStillInHistory(1, oldestSequence = null))
    }
}
