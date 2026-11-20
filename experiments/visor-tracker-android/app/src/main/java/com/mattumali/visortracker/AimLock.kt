package com.mattumali.visortracker

data class AimDecision(
    val lockedId: Int?,
    val changed: Boolean,
    /** True only for a stable closed-hand release. This does not send a command. */
    val released: Boolean = false,
)

/**
 * True when [lockedId] is already missing and some other object in [current]
 * was also present in [previous]. The second AimLock observation can reuse
 * [current]. It does not need another hand and object detect.
 */
object KnownReplacement {
    fun alreadyVisible(previous: FrameDetections?, current: FrameDetections, lockedId: Int): Boolean {
        if (previous == null) return false
        val prior = objectIds(previous)
        val now = objectIds(current)
        if (lockedId in now) return false
        return prior.any { it != lockedId && it in now }
    }

    private fun objectIds(frame: FrameDetections): Set<Int> = frame.boxes.mapNotNull { box ->
        if (box.held || box.kind != "object" || box.trackId <= 0) return@mapNotNull null
        if (box.right <= box.left || box.bottom <= box.top) return@mapNotNull null
        box.trackId
    }.toSet()
}

/**
 * Locks only while a confirmed pointing hand stays on one object.
 * An arm box, an open palm, and the middle of the picture do not acquire.
 * One steady extra frame confirms. Switching takes a longer hold.
 * When the point disappears, the lock is held briefly and then cleared.
 * It is not given to whatever box is left in the picture.
 */
class AimLock(
    private val acquireFrames: Int = 2,
    private val switchFrames: Int = 6,
    private val lostFramesLimit: Int = 3,
    /**
     * Frames required once the locked id is already gone and another object is
     * in this frame. Two rejects a one-frame flicker. This is not added on top
     * of [lostFramesLimit]. The occlusion hold remains only when no replacement
     * is visible.
     *
     * TrackerTuner accepted 2. The 0.25 path waited out the occlusion hold and
     * then the acquire dwell, which is why an emulator spent 4062 ms on several
     * full detect() calls.
     */
    private val knownReplacementFrames: Int = 2,
    private val minHandScore: Float = IndexPoint.MIN_SCORE,
    private val rayHysteresis: Float = IndexPoint.RAY_HYSTERESIS,
    private val releaseFrames: Int = GestureGate.DEFAULT_RELEASE_FRAMES,
) {
    var lockedId: Int? = null
        private set
    /** True after the index finger, not the screen center, confirmed this lock. */
    var fingerLocked: Boolean = false
        private set
    /** A release never calls another app. This stays false. */
    var sentExternalCommand: Boolean = false
        private set
    private val gestures = GestureGate(releaseFrames)
    private var pendingId: Int? = null
    private var pendingCount = 0
    private var lostFrames = 0

    fun reset() {
        lockedId = null
        fingerLocked = false
        sentExternalCommand = false
        gestures.reset()
        pendingId = null
        pendingCount = 0
        lostFrames = 0
    }

    fun observe(frame: FrameDetections): AimDecision = observeOnce(frame, countGesture = true)

    /**
     * Applies the known-replacement count to this one detection.
     * Call this only when [KnownReplacement.alreadyVisible] is true.
     * The extra observations reuse [frame]. They do not run a detector.
     * An empty frame still uses the occlusion hold, one observation at a time.
     */
    fun observeKnownReplacement(frame: FrameDetections): AimDecision {
        var decision = observeOnce(frame, countGesture = true)
        var guard = 0
        while (guard < 8 && pendingId != null && decision.lockedId != null && !decision.changed && !decision.released) {
            decision = observeOnce(frame, countGesture = false)
            guard++
        }
        return decision
    }

    private fun observeOnce(frame: FrameDetections, countGesture: Boolean): AimDecision {
        if (countGesture) {
            val gesture = gestures.observe(frame, locked = lockedId != null)
            if (gesture.sendsExternalCommand) sentExternalCommand = true
            if (gesture.release) return releaseLock()
            if (gesture.gesture == HandGesture.CLOSED && lockedId != null) {
                pendingId = null
                pendingCount = 0
                lostFrames = 0
                return AimDecision(lockedId, changed = false)
            }
        }
        val objects = frame.boxes.filter { box ->
            IndexPoint.pointable(box, frame.width, frame.height)
        }
        val current = lockedId
        val gesture = GestureStates.classify(frame)
        val aimedRay = if (gesture == GestureState.POINTING_CONFIRMED) {
            IndexPoint.aimed(frame, preferId = current, minScore = minHandScore)
        } else {
            null
        }
        val rayTarget = aimedRay?.hit?.trackId
        val visible = current != null && objects.any { it.trackId == current }
        if (gesture == GestureState.ARM_VISIBLE) {
            return dropLock()
        }
        if (gesture == GestureState.HAND_VISIBLE) {
            val openPalm = frame.boxes.filter { it.kind == "hand" && !it.synthetic && it.points.size >= 21 }
                .none { IndexPoint.releaseGesture(it, frame.width, frame.height) }
            if (openPalm) return dropLock()
            if (current == null) return AimDecision(null, changed = false)
            return AimDecision(current, changed = false)
        }
        if (gesture != GestureState.POINTING_CONFIRMED) {
            // Detection missed. Hold the existing lock briefly. Do not pick a
            // center box or the highest score.
            if (current == null) return AimDecision(null, changed = false)
            lostFrames += 1
            if (lostFrames < lostFramesLimit) return AimDecision(current, changed = false)
            return dropLock()
        }
        lostFrames = 0
        if (current != null && !visible) {
            lostFrames += 1
            if (lostFrames < lostFramesLimit) return AimDecision(current, changed = false)
            return dropLock()
        }
        if (rayTarget == null) {
            if (current == null) return AimDecision(null, changed = false)
            lostFrames += 1
            if (lostFrames < lostFramesLimit) return AimDecision(current, changed = false)
            return dropLock()
        }
        val decision = commit(rayTarget, current, if (current == null) acquireFrames else switchFrames)
        if (decision.lockedId != null && decision.lockedId == rayTarget) fingerLocked = true
        return decision
    }

    private fun commit(aimed: Int?, current: Int?, required: Int): AimDecision {
        if (aimed == null || aimed == current) {
            pendingId = null
            pendingCount = 0
            return AimDecision(current, changed = false)
        }
        if (pendingId == aimed) pendingCount += 1 else {
            pendingId = aimed
            pendingCount = 1
        }
        if (pendingCount < required) return AimDecision(current, changed = false)
        lockedId = aimed
        pendingId = null
        pendingCount = 0
        lostFrames = 0
        return AimDecision(aimed, changed = true)
    }

    private fun dropLock(): AimDecision {
        val changed = lockedId != null
        lockedId = null
        fingerLocked = false
        pendingId = null
        pendingCount = 0
        lostFrames = 0
        return AimDecision(null, changed = changed)
    }

    private fun releaseLock(): AimDecision {
        val changed = lockedId != null
        lockedId = null
        fingerLocked = false
        pendingId = null
        pendingCount = 0
        lostFrames = 0
        return AimDecision(null, changed = changed, released = true)
    }
}

/**
 * The object the user is pointing at: the box that contains the center of the
 * picture. When several contain it, the smallest is the one in front. A current
 * lock within twice that area is the same target, not a new one.
 * If the point sits in no box, the nearest center is used.
 * A remembered [BoxObservation.held] rectangle is not a pointable object.
 */
internal fun pointedObject(
    frameWidth: Int,
    frameHeight: Int,
    boxes: List<BoxObservation>,
    preferId: Int? = null,
): BoxObservation? {
    if (frameWidth <= 0 || frameHeight <= 0) return null
    val objects = boxes.filter { box ->
        IndexPoint.pointable(box, frameWidth, frameHeight)
    }
    if (objects.isEmpty()) return null
    val aimX = frameWidth * 0.5f
    val aimY = frameHeight * 0.5f
    val underPoint = objects.filter { box ->
        aimX in box.left..box.right && aimY in box.top..box.bottom
    }
    if (underPoint.isNotEmpty()) {
        val smallest = underPoint.minOf { it.area() }
        val preferred = underPoint.firstOrNull { it.trackId == preferId }
        if (preferred != null && preferred.area() <= smallest * POINT_AREA_TIE) return preferred
        return underPoint.minWith(
            compareBy<BoxObservation> { it.area() }
                .thenBy { centerOffset(it, frameWidth, frameHeight) }
                .thenBy { it.trackId }
        )
    }
    return objects.minWith(
        compareBy<BoxObservation> { centerOffset(it, frameWidth, frameHeight) }
            .thenBy { if (it.trackId == preferId) 0 else 1 }
            .thenBy { it.trackId }
    )
}

private fun centerOffset(box: BoxObservation, width: Int, height: Int): Float {
    val cx = (box.left + box.right) * 0.5f / width - 0.5f
    val cy = (box.top + box.bottom) * 0.5f / height - 0.5f
    return cx * cx + cy * cy
}

/** Boxes under the point and less than twice as large are one target. */
private const val POINT_AREA_TIE = 2f
