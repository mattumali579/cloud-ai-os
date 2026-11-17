package com.mattumali.visortracker

import java.util.Locale
import kotlin.math.ceil

data class PipelineSnapshot(val p95AgeMs: Int, val discarded: Int)

/**
 * Per-window counters for the foreground notification.
 * Ages are milliseconds from camera capture to a published box update.
 * Discards are frames dropped because a newer one was already waiting,
 * not redraws of an unchanged box.
 */
class PipelineStats {
    private val ages = ArrayList<Int>()
    private var discarded = 0

    @Synchronized fun recordAge(ageMs: Int) {
        if (ageMs < 0) return
        if (ages.size >= MAX_SAMPLES) ages.removeAt(0)
        ages += ageMs
    }

    @Synchronized fun recordDiscard(count: Int = 1) {
        if (count > 0) discarded += count
    }

    @Synchronized fun snapshotAndReset(): PipelineSnapshot {
        val snapshot = PipelineSnapshot(percentile95(ages), discarded)
        ages.clear()
        discarded = 0
        return snapshot
    }

    companion object {
        private const val MAX_SAMPLES = 600

        fun percentile95(samples: List<Int>): Int {
            if (samples.isEmpty()) return 0
            val sorted = samples.sorted()
            val rank = ceil(sorted.size * 0.95).toInt().coerceIn(1, sorted.size)
            return sorted[rank - 1]
        }

        fun formatReport(
            videoFps: Float,
            inferenceFps: Float,
            boxFps: Float,
            drawFps: Float,
            ageMs: Int,
            p95AgeMs: Int,
            discarded: Int,
            objects: Int,
        ): String = "V%.0f AI%.0f box%.0f draw%.0f age%dms p95%dms drop%d %dobj".format(
            Locale.US, videoFps, inferenceFps, boxFps, drawFps, ageMs, p95AgeMs, discarded, objects
        )
    }
}
