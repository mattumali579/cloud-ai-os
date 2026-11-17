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
            stages: String = "",
        ): String {
            val core = "V%.0f AI%.0f box%.0f draw%.0f age%dms p95%dms drop%d %dobj".format(
                Locale.US, videoFps, inferenceFps, boxFps, drawFps, ageMs, p95AgeMs, discarded, objects
            )
            return if (stages.isEmpty()) core else "$core $stages"
        }
    }
}

/** Rolling stage times, in milliseconds, for the foreground notification. */
class StageTimings {
    private val samples = HashMap<String, ArrayList<Int>>()

    @Synchronized fun record(name: String, elapsedMs: Long) {
        if (elapsedMs < 0 || elapsedMs > 60_000) return
        val list = samples.getOrPut(name) { ArrayList() }
        if (list.size >= MAX_SAMPLES) list.removeAt(0)
        list += elapsedMs.toInt()
    }

    @Synchronized fun median(name: String): Int {
        val list = samples[name] ?: return 0
        if (list.isEmpty()) return 0
        val sorted = list.sorted()
        return sorted[sorted.size / 2]
    }

    companion object {
        private const val MAX_SAMPLES = 180
    }
}

/**
 * Bounds for the decoder → motion handoff.
 * A gray queue longer than [MAX_GRAY_QUEUE] is replay lag, not smoothness.
 * A detector result that can no longer be walked forward onto a recent
 * grayscale frame is dropped instead of painted at its old coordinates.
 */
object FrameBudget {
    const val MAX_GRAY_QUEUE = 6
    const val MAX_BOX_AGE_MS = 300L

    fun coalesceNewest(frames: List<GrayFrame>, maxKeep: Int): Pair<List<GrayFrame>, Int> {
        if (maxKeep <= 0) return emptyList<GrayFrame>() to frames.size
        if (frames.size <= maxKeep) return frames to 0
        return frames.takeLast(maxKeep) to frames.size - maxKeep
    }

    fun shouldDropStaleDetection(canRebase: Boolean, ageMs: Long): Boolean =
        !canRebase && ageMs > MAX_BOX_AGE_MS
}
