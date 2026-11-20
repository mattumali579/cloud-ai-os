package com.mattumali.visortracker

import kotlin.math.abs
import kotlin.math.ceil

/**
 * Timestamps for one locked target. Redrawing an unchanged rectangle is not a sample.
 * Intervals use the capture time of frames the motion tracker actually stepped.
 */
data class LockSnapshot(
    val decoded: Int,
    val processed: Int,
    val intervalP50: Int,
    val intervalP95: Int,
    val intervalP99: Int,
    val ageP50: Int,
    val ageP95: Int,
    val ageP99: Int,
    val dropped: Int,
    val staleDetections: Int,
    /** p95 center deviation divided by frame width. Negative when the window was moving. */
    val jitterP95Fraction: Float,
    val idSwitches: Int,
    val lockLosses: Int,
    /** Negative when the window had no optical-flow reacquire. */
    val reacquireP95Ms: Int,
) {
    fun processedFraction(): Float =
        if (decoded <= 0) 0f else processed.toFloat() / decoded.toFloat()
}

class LockMetrics {
    private val intervals = ArrayList<Int>()
    private val ages = ArrayList<Int>()
    private val centers = ArrayList<Float>()
    private val reacquires = ArrayList<Int>()
    private var decoded = 0
    private var processed = 0
    private var dropped = 0
    private var staleDetections = 0
    private var idSwitches = 0
    private var lockLosses = 0
    private var lastCapture = -1L
    private var lastCenter: Float? = null
    private var centerWidth = 0
    private var lastLockedId: Int? = null

    @Synchronized fun onDecoded() {
        decoded++
    }

    @Synchronized fun onTrackDrop(count: Int) {
        if (count > 0) dropped += count
    }

    /** A camera frame the motion tracker stepped. Unchanged coordinates still count. */
    @Synchronized fun onProcessed(captureMs: Long) {
        processed++
        if (lastCapture >= 0L && captureMs >= lastCapture) {
            val delta = (captureMs - lastCapture).toInt()
            if (intervals.size >= MAX_SAMPLES) intervals.removeAt(0)
            intervals += delta
        }
        if (captureMs >= 0L) lastCapture = captureMs
    }

    /**
     * A box that was actually published. [captureMs] is the gray frame the box
     * was aligned to, not the detector's start time.
     */
    @Synchronized fun onPublished(
        captureMs: Long,
        nowMs: Long,
        centerX: Float,
        frameWidth: Int,
        lockedId: Int?,
        publishedId: Int?,
    ) {
        if (captureMs > 0L && nowMs >= captureMs) {
            if (ages.size >= MAX_SAMPLES) ages.removeAt(0)
            ages += (nowMs - captureMs).toInt()
        }
        if (frameWidth > 0 && centerX.isFinite()) {
            val previous = lastCenter
            if (previous != null && centerWidth == frameWidth &&
                abs(centerX - previous) > frameWidth * STATIONARY_FRACTION
            ) {
                centers.clear()
            }
            if (centers.size >= MAX_SAMPLES) centers.removeAt(0)
            centers += centerX
            lastCenter = centerX
            centerWidth = frameWidth
        }
        if (lockedId != null && lastLockedId != null && publishedId != null && publishedId != lastLockedId) {
            idSwitches++
        }
        if (publishedId != null) lastLockedId = publishedId
    }

    @Synchronized fun onLockLoss() {
        lockLosses++
    }

    @Synchronized fun onReacquire(latencyMs: Int) {
        if (latencyMs < 0) return
        if (reacquires.size >= MAX_SAMPLES) reacquires.removeAt(0)
        reacquires += latencyMs
    }

    @Synchronized fun onStaleDetection() {
        staleDetections++
    }

    @Synchronized fun snapshotAndReset(): LockSnapshot {
        val snapshot = LockSnapshot(
            decoded = decoded,
            processed = processed,
            intervalP50 = PipelineStats.percentile(intervals, 0.50),
            intervalP95 = PipelineStats.percentile(intervals, 0.95),
            intervalP99 = PipelineStats.percentile(intervals, 0.99),
            ageP50 = PipelineStats.percentile(ages, 0.50),
            ageP95 = PipelineStats.percentile(ages, 0.95),
            ageP99 = PipelineStats.percentile(ages, 0.99),
            dropped = dropped,
            staleDetections = staleDetections,
            jitterP95Fraction = jitterFraction(centers, centerWidth),
            idSwitches = idSwitches,
            lockLosses = lockLosses,
            reacquireP95Ms = if (reacquires.isEmpty()) -1 else PipelineStats.percentile(reacquires, 0.95),
        )
        intervals.clear()
        ages.clear()
        centers.clear()
        reacquires.clear()
        decoded = 0
        processed = 0
        dropped = 0
        staleDetections = 0
        idSwitches = 0
        lockLosses = 0
        lastCapture = -1L
        lastCenter = null
        centerWidth = 0
        lastLockedId = null
        return snapshot
    }

    companion object {
        private const val MAX_SAMPLES = 600
        private const val STATIONARY_FRACTION = 0.01f

        fun jitterFraction(samples: List<Float>, frameWidth: Int): Float {
            if (samples.size < 5 || frameWidth <= 0) return -1f
            val sorted = samples.sorted()
            val median = sorted[sorted.size / 2]
            val deviations = samples.map { abs(it - median) / frameWidth }
            val ordered = deviations.sorted()
            val rank = ceil(ordered.size * 0.95).toInt().coerceIn(1, ordered.size)
            return ordered[rank - 1]
        }
    }
}
