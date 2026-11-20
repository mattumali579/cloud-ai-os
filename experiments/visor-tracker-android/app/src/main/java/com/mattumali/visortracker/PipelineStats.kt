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

        fun percentile(samples: List<Int>, fraction: Double): Int {
            if (samples.isEmpty()) return 0
            val sorted = samples.sorted()
            val rank = ceil(sorted.size * fraction).toInt().coerceIn(1, sorted.size)
            return sorted[rank - 1]
        }

        fun percentile95(samples: List<Int>): Int = percentile(samples, 0.95)

        /**
         * One notification line for the performance contract.
         * V is decoded video FPS. trk is frames the motion tracker actually stepped.
         * box counts coordinate or label changes, not redraws of the same rectangle.
         * i and a are p50/p95/p99 of tracker spacing and decoded-frame age, in ms.
         */
        fun formatReport(
            videoFps: Float,
            inferenceFps: Float,
            trackFps: Float,
            boxFps: Float,
            drawFps: Float,
            lock: LockSnapshot,
            objects: Int,
            stages: String = "",
        ): String {
            val jit = if (lock.jitterP95Fraction < 0f) "jit-na" else "jit%.2f%%".format(
                Locale.US, lock.jitterP95Fraction * 100f
            )
            val reacquire = if (lock.reacquireP95Ms < 0) "ra-" else "ra${lock.reacquireP95Ms}"
            val core = ("V%.0f AI%.0f trk%.0f box%.0f draw%.0f i%d/%d/%d a%d/%d/%d " +
                "drop%d stale%d %s id%d loss%d %s %dobj").format(
                Locale.US,
                videoFps, inferenceFps, trackFps, boxFps, drawFps,
                lock.intervalP50, lock.intervalP95, lock.intervalP99,
                lock.ageP50, lock.ageP95, lock.ageP99,
                lock.dropped, lock.staleDetections, jit, lock.idSwitches, lock.lockLosses,
                reacquire, objects,
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
 * The live slot keeps one frame. [REBASE_HISTORY] is only for walking a slow
 * detector result forward. A result whose grayscale anchor has already fallen
 * out of that history does not move a box that motion is already following.
 * If nothing is showing, that detection is still painted.
 */
object FrameBudget {
    const val MAX_GRAY_QUEUE = 6
    const val REBASE_HISTORY = 8
    const val MAX_BOX_AGE_MS = 300L

    /** False when frames between the detector anchor and now were already discarded. */
    fun anchorStillInHistory(anchorSequence: Long, oldestSequence: Long?): Boolean =
        oldestSequence == null || anchorSequence >= oldestSequence

    fun coalesceNewest(frames: List<GrayFrame>, maxKeep: Int): Pair<List<GrayFrame>, Int> {
        if (maxKeep <= 0) return emptyList<GrayFrame>() to frames.size
        if (frames.size <= maxKeep) return frames to 0
        return frames.takeLast(maxKeep) to frames.size - maxKeep
    }

    fun shouldDropStaleDetection(canRebase: Boolean, ageMs: Long): Boolean =
        !canRebase && ageMs > MAX_BOX_AGE_MS
}
