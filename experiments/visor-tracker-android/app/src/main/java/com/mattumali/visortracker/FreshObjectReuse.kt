package com.mattumali.visortracker

/**
 * The object model can stay idle when another box was already in a recent frame.
 * This does not change [AimLock]'s occlusion hold. An empty scene is not fresh.
 */
object FreshObjectReuse {
    /** One locked cadence, plus a little slack so the next due pass can still reuse it. */
    const val MAX_AGE_MS = 400L

    fun replacementIsFresh(previous: FrameDetections?, lockedId: Int, ageMs: Long): Boolean {
        if (previous == null || lockedId <= 0 || ageMs < 0L || ageMs > MAX_AGE_MS) return false
        return previous.boxes.any { box ->
            box.kind == "object" && !box.held && box.trackId > 0 && box.trackId != lockedId &&
                box.right > box.left && box.bottom > box.top
        }
    }
}

/**
 * Whether this pass should call EfficientDet.
 * A fresh replacement whose locked rectangle has changed skips one object pass.
 * The following due pass runs the model. Hands are a separate decision.
 */
object ObjectSkip {
    fun runObjects(
        due: Boolean,
        replacementFresh: Boolean,
        obscured: Boolean,
        alreadySkipped: Boolean,
    ): Boolean {
        if (replacementFresh && obscured && !alreadySkipped) return false
        return due
    }
}
