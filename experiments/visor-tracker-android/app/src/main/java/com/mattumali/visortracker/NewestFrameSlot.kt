package com.mattumali.visortracker

import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * One waiting frame. A newer offer displaces the previous one.
 * The slot never grows into a queue. The caller releases the displaced frame.
 */
class NewestFrameSlot<T> {
    private val slot = AtomicReference<T?>(null)
    private val drops = AtomicInteger(0)

    val dropped: Int get() = drops.get()

    fun offer(frame: T): T? {
        val previous = slot.getAndSet(frame)
        if (previous != null) drops.incrementAndGet()
        return previous
    }

    fun poll(): T? = slot.getAndSet(null)

    fun hasPending(): Boolean = slot.get() != null
}
