package com.mattumali.visortracker

import kotlin.math.hypot

/** Snapshot of a verified point that currently has no local target candidate. */
data class GroundingHint(
    val sequence: Long,
    val frameWidth: Int,
    val frameHeight: Int,
    val tipX: Float,
    val tipY: Float,
    val dirX: Float,
    val dirY: Float,
    val eligible: Boolean,
)

/**
 * Limits cloud grounding to a sustained, directionally stable point with no
 * local ray hit. One request is allowed per pointing episode and requests have
 * a wall-clock cooldown. This class never selects a target.
 */
class GroundingRequestGate(
    private val stableFramesRequired: Int = 6,
    private val cooldownMs: Long = 4_000L,
) {
    private var stableFrames = 0
    private var lastDirX: Float? = null
    private var lastDirY: Float? = null
    private var lastTipX: Float? = null
    private var lastTipY: Float? = null
    private var requestedThisEpisode = false
    private var requestInFlight = false
    private var lastRequestAtMs = Long.MIN_VALUE

    @Synchronized
    fun shouldRequest(hint: GroundingHint?, nowMs: Long): Boolean {
        if (hint == null || !hint.eligible || hint.frameWidth <= 0 || hint.frameHeight <= 0) {
            resetEpisode()
            return false
        }
        val previousX = lastDirX
        val previousY = lastDirY
        val previousTipX = lastTipX
        val previousTipY = lastTipY
        val directionStable = previousX == null || previousY == null ||
            previousX * hint.dirX + previousY * hint.dirY >= 0.96f
        val tipStable = previousTipX == null || previousTipY == null ||
            hypot(hint.tipX - previousTipX, hint.tipY - previousTipY) <=
            hypot(hint.frameWidth.toFloat(), hint.frameHeight.toFloat()) * 0.06f
        stableFrames = if (directionStable && tipStable) stableFrames + 1 else 1
        lastDirX = hint.dirX
        lastDirY = hint.dirY
        lastTipX = hint.tipX
        lastTipY = hint.tipY
        val cooldownElapsed = lastRequestAtMs == Long.MIN_VALUE || nowMs - lastRequestAtMs >= cooldownMs
        if (stableFrames < stableFramesRequired || requestedThisEpisode || requestInFlight || !cooldownElapsed) {
            return false
        }
        requestInFlight = true
        requestedThisEpisode = true
        lastRequestAtMs = nowMs
        return true
    }

    @Synchronized
    fun complete() {
        requestInFlight = false
    }

    @Synchronized
    fun reset() {
        lastRequestAtMs = Long.MIN_VALUE
        requestInFlight = false
        resetEpisode()
    }

    private fun resetEpisode() {
        stableFrames = 0
        lastDirX = null
        lastDirY = null
        lastTipX = null
        lastTipY = null
        requestedThisEpisode = false
    }
}

/** Short-lived, untracked VLM proposals. The spatial engine still has to hit and confirm them. */
class GroundedCandidateCache(
    private val ttlMs: Long = 1_200L,
) {
    private var expiresAtMs = 0L
    private var candidates: List<BoxObservation> = emptyList()
    private var requestHint: GroundingHint? = null

    @Synchronized
    fun replace(boxes: List<BoxObservation>, hint: GroundingHint, nowMs: Long) {
        candidates = boxes.mapIndexed { index, box ->
            box.copy(trackId = GROUNDED_TRACK_BASE + index, held = false)
        }
        requestHint = if (candidates.isEmpty()) null else hint
        expiresAtMs = if (candidates.isEmpty()) 0L else nowMs + ttlMs
    }

    @Synchronized
    fun append(frame: FrameDetections, currentHint: GroundingHint?, nowMs: Long): FrameDetections {
        val origin = requestHint
        val samePointingEpisode = origin != null && currentHint != null &&
            origin.dirX * currentHint.dirX + origin.dirY * currentHint.dirY >= 0.96f &&
            hypot(origin.tipX - currentHint.tipX, origin.tipY - currentHint.tipY) <=
            hypot(origin.frameWidth.toFloat(), origin.frameHeight.toFloat()) * 0.06f
        if (nowMs > expiresAtMs || candidates.isEmpty() || !samePointingEpisode) {
            clear()
            return frame
        }
        return frame.copy(boxes = frame.boxes + candidates)
    }

    @Synchronized
    fun clear() {
        candidates = emptyList()
        expiresAtMs = 0L
        requestHint = null
    }

    companion object {
        const val GROUNDED_TRACK_BASE = 1_000_000
    }
}
