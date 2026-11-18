package com.mattumali.visortracker

data class AimDecision(val lockedId: Int?, val changed: Boolean)

/**
 * Locks the object under the middle of the picture.
 * The first box is drawn before this confirms. One more steady frame locks it.
 * Switching takes the same short hold, so one jittery frame does not swap targets.
 * A lock that still covers the aim point stays put unless a clearly smaller
 * object is the one under that point.
 */
class AimLock(
    private val acquireFrames: Int = 2,
    private val switchFrames: Int = 2,
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
        val aimed = pointedObject(frame.width, frame.height, frame.boxes, preferId = lockedId)?.trackId
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
        !box.held && box.kind == "object" && box.trackId > 0 &&
            box.right > box.left && box.bottom > box.top
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
