package com.mattumali.visortracker

enum class HandGesture { POINTING, CLOSED, ABSENT, OTHER }

/**
 * What the hand is doing on this frame.
 * [sendsExternalCommand] stays false. A release does not call Jarvis or any other app.
 */
data class GestureDecision(
    val gesture: HandGesture,
    val release: Boolean,
    val sendsExternalCommand: Boolean = false,
)

/**
 * Sits on top of the landmarks [IndexPoint] already uses. No second model.
 * An extended index is pointing. A missing hand after a finger lock is a hold,
 * decided by [AimLock], not by releasing here. A closed fist or a fully curled
 * index does not switch the lock. Release becomes true only after that same
 * closed gesture has been stable for [releaseFrames] frames. One frame never
 * releases when [releaseFrames] is greater than 1.
 */
class GestureGate(
    private val releaseFrames: Int = DEFAULT_RELEASE_FRAMES,
) {
    private var stable = 0

    fun reset() {
        stable = 0
    }

    fun classify(frame: FrameDetections): HandGesture {
        val hands = frame.boxes.filter { box ->
            box.kind == "hand" && !box.held && box.points.size >= 21 && box.score >= IndexPoint.MIN_SCORE
        }
        if (hands.isEmpty()) return HandGesture.ABSENT
        if (hands.any { IndexPoint.extended(it, frame.width, frame.height) != null }) {
            return HandGesture.POINTING
        }
        if (hands.any { IndexPoint.releaseGesture(it, frame.width, frame.height) }) return HandGesture.CLOSED
        return HandGesture.OTHER
    }

    fun observe(frame: FrameDetections, locked: Boolean): GestureDecision {
        val gesture = classify(frame)
        if (!locked || gesture != HandGesture.CLOSED) {
            stable = 0
            return GestureDecision(gesture, release = false)
        }
        stable += 1
        val release = stable >= releaseFrames.coerceAtLeast(1)
        if (release) stable = 0
        return GestureDecision(gesture, release = release)
    }

    companion object {
        const val DEFAULT_RELEASE_FRAMES = 4
    }
}
