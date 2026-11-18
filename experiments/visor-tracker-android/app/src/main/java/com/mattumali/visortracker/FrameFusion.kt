package com.mattumali.visortracker

/**
 * Publishes detector results in capture order.
 * A result for an older sequence cannot replace a newer lock or a newer frame.
 * Hand and object workers may both call [offer]. An older sequence is dropped.
 */
class FrameFusion {
    private var publishedSequence = Long.MIN_VALUE
    @Volatile var dropped: Int = 0
        private set
    @Volatile var lastAgeMs: Long = -1L
        private set

    fun offer(frame: FrameDetections, nowMs: Long): FrameDetections? = synchronized(this) {
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
        frame
    }

    fun reset() = synchronized(this) {
        publishedSequence = Long.MIN_VALUE
        dropped = 0
        lastAgeMs = -1L
    }
}
