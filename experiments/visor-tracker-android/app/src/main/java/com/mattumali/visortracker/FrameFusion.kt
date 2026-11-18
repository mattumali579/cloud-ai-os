package com.mattumali.visortracker

/**
 * Publishes detector results in capture order.
 * A result for an older sequence cannot replace a newer lock or a newer frame.
 * Called on the inference worker only.
 */
class FrameFusion {
    private var publishedSequence = Long.MIN_VALUE
    var dropped: Int = 0
        private set
    var lastAgeMs: Long = -1L
        private set

    fun offer(frame: FrameDetections, nowMs: Long): FrameDetections? {
        if (frame.sequence < publishedSequence) {
            dropped += 1
            return null
        }
        publishedSequence = frame.sequence
        lastAgeMs = if (frame.capturedAtMs > 0L && nowMs >= frame.capturedAtMs) {
            nowMs - frame.capturedAtMs
        } else {
            -1L
        }
        return frame
    }

    fun reset() {
        publishedSequence = Long.MIN_VALUE
        dropped = 0
        lastAgeMs = -1L
    }
}
