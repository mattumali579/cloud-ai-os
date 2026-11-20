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
 * Locks the object under the middle of the picture.
 * The first box is drawn before this confirms. One more steady frame locks it.
 * Switching takes the same short hold, so one jittery frame does not swap targets.
 * A lock that still covers the aim point stays put unless a clearly smaller
 * object is the one under that point.
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
        val ray = IndexPoint.ray(frame, minHandScore)
        val current = lockedId
        val visible = current != null && objects.any { it.trackId == current }
        if (current != null && !visible) {
            val next = if (ray != null) {
                IndexPoint.alongRay(
                    ray, objects, preferId = null, hysteresis = rayHysteresis,
                    frameWidth = frame.width, frameHeight = frame.height,
                )?.trackId
            } else {
                IndexPoint.replacement(objects, current)?.trackId
            }
            if (next != null) {
                // The replacement is already in this frame. Do not wait out the
                // occlusion hold and then acquire again.
                lostFrames = 0
                val decision = commit(next, current, knownReplacementFrames)
                if (ray != null && decision.changed && decision.lockedId == next) fingerLocked = true
                return decision
            }
            pendingId = null
            pendingCount = 0
            lostFrames += 1
            if (lostFrames < lostFramesLimit) return AimDecision(current, changed = false)
            lockedId = null
            fingerLocked = false
            lostFrames = 0
            return AimDecision(null, changed = true)
        }
        lostFrames = 0
        val rayTarget = if (ray != null) {
            IndexPoint.alongRay(
                ray, objects, current, rayHysteresis,
                frameWidth = frame.width, frameHeight = frame.height,
            )?.trackId
        } else {
            null
        }
        val aimed = when {
            rayTarget != null -> rayTarget
            current != null -> current
            else -> pointedObject(frame.width, frame.height, frame.boxes, preferId = current)?.trackId
        }
        val decision = commit(aimed, current, if (current == null) acquireFrames else switchFrames)
        if (ray != null && decision.lockedId != null && decision.lockedId == rayTarget) fingerLocked = true
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
