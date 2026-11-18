package com.mattumali.visortracker

/**
 * One pipeline at a time. Reconnect clears the published sequence so an old
 * frame cannot land on the new stream. Closing the previous pipeline is the
 * caller's job when [detach] returns it, or [attach] closes the previous one.
 */
class TrackerSession<T : AutoCloseable> {
    var pipeline: T? = null
        private set
    val fusion = FrameFusion()
    var closeCount: Int = 0
        private set

    fun attach(next: T) {
        val previous = pipeline
        pipeline = next
        if (previous != null) {
            previous.close()
            closeCount += 1
        }
        fusion.reset()
    }

    fun onReconnect(handoff: TrackingHandoff) {
        handoff.reset()
        fusion.reset()
    }

    /** Removes the pipeline without closing it. The caller closes it once. */
    fun detach(): T? {
        val current = pipeline
        pipeline = null
        fusion.reset()
        return current
    }
}
