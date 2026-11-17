package com.mattumali.visortracker

import java.util.ArrayDeque

data class DetectionBaseline(val detections: FrameDetections, val gray: GrayFrame)

data class HandoffEvent(val frame: FrameDetections, val lost: Boolean)

/**
 * How often the heavyweight detector may run.
 * After a target is locked this stays inside 2–5 Hz. The per-frame path is [MotionTracker].
 */
object InferenceCadence {
    /** 5 Hz while the user is still choosing a target. */
    const val SEEK_MS = 200L
    /** 4 Hz once one object is locked. Classification is not the position tracker. */
    const val LOCKED_MS = 250L

    fun intervalMs(locked: Boolean): Long = if (locked) LOCKED_MS else SEEK_MS

    fun due(nowMs: Long, lastOfferedMs: Long, locked: Boolean): Boolean {
        if (lastOfferedMs <= 0L) return true
        return nowMs - lastOfferedMs >= intervalMs(locked)
    }
}

/**
 * Decoder to motion handoff.
 *
 * The work slot holds one gray frame. A newer frame replaces it and counts a drop,
 * so a slow step cannot replay a backlog and present old coordinates as live.
 * [FrameBudget.REBASE_HISTORY] exists so a detector result can be walked forward
 * onto the newest gray frame. If that walk cannot follow every frame, the
 * detection itself is still drawn. A detection older than the retained
 * history is not drawn.
 */
class TrackingHandoff(
    val metrics: LockMetrics = LockMetrics(),
    private var tracker: MotionTracker = MotionTracker(),
) {
    private val gate = Any()
    private var pending: GrayFrame? = null
    private val history = ArrayDeque<GrayFrame>()
    private var pendingDrops = 0
    private var armed = false
    private var showing = false
    private var inLoss = false
    private var publishedSequence = Long.MIN_VALUE
    private var lastProcessedSequence = Long.MIN_VALUE

    fun offer(frame: GrayFrame): Int = synchronized(gate) {
        metrics.onDecoded()
        history.addLast(frame)
        while (history.size > FrameBudget.REBASE_HISTORY) history.removeFirst()
        val dropped = if (pending != null) 1 else 0
        pending = frame
        if (dropped > 0) pendingDrops += dropped
        dropped
    }

    fun hasPending(): Boolean = synchronized(gate) { pending != null }

    fun reset() {
        synchronized(gate) {
            pending = null
            history.clear()
            pendingDrops = 0
        }
        tracker = MotionTracker()
        armed = false
        showing = false
        inLoss = false
        publishedSequence = Long.MIN_VALUE
        lastProcessedSequence = Long.MIN_VALUE
    }

    /**
     * Step the newest pending frame, or walk [detection] onto that frame.
     * Returns the single frame the overlay may draw. Null means nothing new to draw.
     */
    fun pump(nowMs: Long, detection: DetectionBaseline?, lockedId: Int?): HandoffEvent? {
        val frame: GrayFrame?
        val historyCopy: List<GrayFrame>
        val dropped: Int
        synchronized(gate) {
            frame = pending
            pending = null
            historyCopy = history.toList()
            dropped = pendingDrops
            pendingDrops = 0
        }
        if (dropped > 0) metrics.onTrackDrop(dropped)
        if (detection != null) {
            val walked = tryWalk(detection, historyCopy, nowMs, lockedId)
            if (walked != null) return walked
        }
        if (frame != null && armed) return stepLive(frame, nowMs, lockedId)
        return null
    }

    private fun tryWalk(
        detection: DetectionBaseline,
        historyCopy: List<GrayFrame>,
        nowMs: Long,
        lockedId: Int?,
    ): HandoffEvent? {
        val focused = focus(detection.detections, lockedId) ?: return null
        val oldest = historyCopy.firstOrNull()?.sequence
        if (!FrameBudget.anchorStillInHistory(detection.gray.sequence, oldest)) {
            metrics.onStaleDetection()
            return null
        }
        val scratch = MotionTracker()
        scratch.reset(focused, detection.gray)
        val after = historyCopy
            .filter { it.sequence > detection.gray.sequence }
            .sortedBy { it.sequence }
        var latest = focused.copy(
            sequence = detection.gray.sequence,
            capturedAtMs = detection.gray.capturedAtMs,
        )
        var seedGray = detection.gray
        var followedEveryFrame = true
        for (gray in after) {
            val step = scratch.step(gray, nowMs)
            if (step.frame == null || step.frame.boxes.isEmpty()) {
                followedEveryFrame = false
                break
            }
            latest = step.frame
            seedGray = gray
        }
        // A failed block match used to return null, so the overlay stayed blank
        // on a real Visor stream. Draw the last boxes we could still see.
        tracker = if (followedEveryFrame) {
            scratch
        } else {
            MotionTracker().also { it.reset(latest, seedGray) }
        }
        armed = true
        markProcessed(detection.gray)
        if (followedEveryFrame) {
            for (gray in after) markProcessed(gray)
        }
        inLoss = false
        showing = latest.boxes.isNotEmpty()
        return publish(latest, nowMs, lockedId, lost = false)
    }

    private fun stepLive(frame: GrayFrame, nowMs: Long, lockedId: Int?): HandoffEvent? {
        markProcessed(frame)
        val step = tracker.step(frame, nowMs)
        val result = step.frame
        if (result == null || result.boxes.isEmpty()) {
            val firstLoss = !inLoss && (showing || step.lost)
            inLoss = true
            if (!firstLoss) {
                if (frame.sequence > publishedSequence) publishedSequence = frame.sequence
                return null
            }
            if (step.lost) metrics.onLockLoss()
            showing = false
            val empty = FrameDetections(
                width = result?.width ?: frame.width,
                height = result?.height ?: frame.height,
                boxes = emptyList(),
                sequence = frame.sequence,
                capturedAtMs = frame.capturedAtMs,
            )
            return publish(empty, nowMs, lockedId, lost = true)
        }
        if (step.reacquired) metrics.onReacquire(step.reacquireMs)
        inLoss = false
        showing = true
        return publish(result, nowMs, lockedId, lost = false)
    }

    private fun publish(
        frame: FrameDetections,
        nowMs: Long,
        lockedId: Int?,
        lost: Boolean,
    ): HandoffEvent? {
        if (frame.sequence < publishedSequence) return null
        publishedSequence = frame.sequence
        if (!lost) {
            val box = frame.boxes.firstOrNull { lockedId == null || it.trackId == lockedId }
                ?: frame.boxes.firstOrNull()
            val center = if (box == null) Float.NaN else (box.left + box.right) / 2f
            metrics.onPublished(
                captureMs = frame.capturedAtMs,
                nowMs = nowMs,
                centerX = center,
                frameWidth = frame.width,
                lockedId = lockedId,
                publishedId = box?.trackId,
            )
        }
        return HandoffEvent(frame, lost)
    }

    private fun markProcessed(frame: GrayFrame) {
        if (frame.sequence <= lastProcessedSequence) return
        lastProcessedSequence = frame.sequence
        metrics.onProcessed(frame.capturedAtMs)
    }

    private fun focus(detections: FrameDetections, lockedId: Int?): FrameDetections? {
        val visible = detections.boxes.filter { !it.held }
        if (visible.isEmpty()) return null
        if (lockedId == null) return detections.copy(boxes = visible)
        val locked = visible.filter { it.trackId == lockedId }
        if (locked.isEmpty()) return null
        return detections.copy(boxes = locked)
    }
}
