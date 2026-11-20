package com.mattumali.visortracker

/**
 * Keeps the last real detection through a short gap.
 * An empty motion result must not erase a box that was just drawn.
 */
object BoxHold {
    const val HOLD_MS = 2_000L
    /** Overlay never keeps a vanished hand. Zero means no ghost skeleton. */
    const val HAND_HOLD_MS = 0L

    fun keep(
        previous: List<BoxObservation>,
        incoming: List<BoxObservation>,
        ageMs: Long,
    ): List<BoxObservation> {
        if (incoming.isNotEmpty()) return incoming
        if (previous.isNotEmpty() && ageMs in 0..HOLD_MS) return previous
        return incoming
    }
}
