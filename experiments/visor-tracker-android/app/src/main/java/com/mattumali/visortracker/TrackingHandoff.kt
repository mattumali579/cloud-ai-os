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
 * onto the newest gray frame. If that walk cannot follow every frame, or the
 * anchor has already left the history, the detection is still drawn when the
 * overlay would otherwise stay empty. A live motion lock is not moved by a
 * stale result.
 */
class TrackingHandoff(
    val metrics: LockMetrics = LockMetrics(),
    private var tracker: MotionTracker = MotionTracker(),
    private val display: NearbyCandidates = NearbyCandidates(),
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
    /** Track last given to the overlay. A new point must replace this id. */
    private var shownTrackId: Int? = null

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
        shownTrackId = null
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
            // A late result for the track already on screen must not jump it.
            // A different track is a new point: keeping the old motion box
            // would leave that lock up after the user moved on.
            val sameTarget = focused.boxes.firstOrNull()?.trackId == shownTrackId
            if (showing && sameTarget) return null
            return publishDetached(focused, historyCopy, detection.gray, nowMs, lockedId)
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
        latest = stampLive(latest, historyCopy)
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

    /** Draw a detector box that can no longer be walked onto the live frame. */
    private fun publishDetached(
        focused: FrameDetections,
        historyCopy: List<GrayFrame>,
        anchor: GrayFrame,
        nowMs: Long,
        lockedId: Int?,
    ): HandoffEvent? {
        val seed = historyCopy.lastOrNull() ?: anchor
        val stamped = stampLive(focused, historyCopy)
        tracker = MotionTracker().also { it.reset(stamped, seed) }
        armed = true
        inLoss = false
        showing = stamped.boxes.isNotEmpty()
        markProcessed(seed)
        return publish(stamped, nowMs, lockedId, lost = false)
    }

    private fun stampLive(frame: FrameDetections, historyCopy: List<GrayFrame>): FrameDetections {
        val newest = historyCopy.lastOrNull()
        val sequence = maxOf(frame.sequence, newest?.sequence ?: frame.sequence, publishedSequence)
        if (sequence == frame.sequence) return frame
        return frame.copy(
            sequence = sequence,
            capturedAtMs = newest?.capturedAtMs ?: frame.capturedAtMs,
        )
    }

    private fun stepLive(frame: GrayFrame, nowMs: Long, lockedId: Int?): HandoffEvent? {
        markProcessed(frame)
        val step = tracker.step(frame, nowMs)
        val result = step.frame
        if (result == null || result.boxes.isEmpty()) {
            val firstLoss = !inLoss && (showing || step.lost)
            inLoss = true
            showing = false
            if (firstLoss && step.lost) metrics.onLockLoss()
            if (frame.sequence > publishedSequence) publishedSequence = frame.sequence
            // Publishing an empty frame replaced the detector box before onDraw.
            // Keep the last box until the next detection or a successful track.
            return null
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
        val visible = frame.boxes.firstOrNull { !it.held }
        if (visible != null) shownTrackId = visible.trackId
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
        val objects = detections.boxes.filter {
            !it.held && it.kind == "object" && it.trackId > 0 &&
                it.right > it.left && it.bottom > it.top
        }
        if (objects.isEmpty()) return null
        val lockedPresent = lockedId != null && objects.any { it.trackId == lockedId }
        // One foreign detection must not steal a live lock. AimLock changes
        // the id after two centered frames, and the next pass draws that id.
        if (lockedId != null && !lockedPresent && showing) return null
        val chosen = display.select(
            detections.width,
            detections.height,
            objects,
            if (lockedPresent) lockedId else null,
        )
        if (chosen.isEmpty()) return null
        return detections.copy(boxes = chosen)
    }
}
