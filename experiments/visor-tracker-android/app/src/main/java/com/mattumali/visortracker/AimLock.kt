package com.mattumali.visortracker

import kotlin.math.hypot

data class AimDecision(val lockedId: Int?, val changed: Boolean)

/**
 * Locks the object the glasses are pointing at: the box nearest the middle
 * of the picture. A new target must stay there for several frames before the
 * lock moves, so motion and box jitter do not swap targets every frame.
 */
class AimLock(
    private val acquireFrames: Int = 3,
    private val switchFrames: Int = 5,
    private val centerRadius: Float = 0.22f,
) {
    var lockedId: Int? = null
        private set
    private var pendingId: Int? = null
    private var pendingCount = 0

    fun reset() {
        lockedId = null
        pendingId = null
        pendingCount = 0
    }

    fun observe(frame: FrameDetections): AimDecision {
        val aimed = nearestCentered(frame)
        val current = lockedId
        if (aimed == null || aimed == current) {
            pendingId = null
            pendingCount = 0
            return AimDecision(current, changed = false)
        }
        if (pendingId == aimed) pendingCount += 1 else {
            pendingId = aimed
            pendingCount = 1
        }
        val required = if (current == null) acquireFrames else switchFrames
        if (pendingCount < required) return AimDecision(current, changed = false)
        lockedId = aimed
        pendingId = null
        pendingCount = 0
        return AimDecision(aimed, changed = true)
    }

    private fun nearestCentered(frame: FrameDetections): Int? {
        if (frame.width <= 0 || frame.height <= 0) return null
        var bestId: Int? = null
        var best = Float.MAX_VALUE
        for (box in frame.boxes) {
            if (box.kind != "object" || box.trackId <= 0) continue
            if (box.right <= box.left || box.bottom <= box.top) continue
            val cx = (box.left + box.right) * 0.5f / frame.width
            val cy = (box.top + box.bottom) * 0.5f / frame.height
            val distance = hypot(cx - 0.5f, cy - 0.5f)
            if (distance <= centerRadius && distance < best) {
                best = distance
                bestId = box.trackId
            }
        }
        return bestId
    }
}
