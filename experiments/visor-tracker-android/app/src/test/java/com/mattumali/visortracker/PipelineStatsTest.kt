package com.mattumali.visortracker

import org.junit.Assert.assertEquals
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
    }
}
