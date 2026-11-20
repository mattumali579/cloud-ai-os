package com.mattumali.visortracker

/**
 * Whether a detector pass may start.
 * Grayscale success is intentionally not an input. A broken gray frame must
 * not stop RGB inference.
 */
object DetectorSchedule {
    fun shouldStart(inferenceDue: Boolean, workerFree: Boolean): Boolean =
        inferenceDue && workerFree
}

data class ModelPass(val objects: Boolean, val hands: Boolean)

/**
 * Hands and objects do not share one cadence.
 * After a lock, object detection stays on [InferenceCadence] (4 Hz). Hands can
 * run on the frames in between so a finger update does not wait for EfficientDet.
 * EfficientDet stays on the inference worker. The hand landmarker stays on its
 * own thread. One detector instance is never called from two threads, and the
 * shared YUV bitmap is not passed to both models.
 */
object SplitSchedule {
    const val HAND_MS = 66L

    fun decide(
        nowMs: Long,
        lastObjectMs: Long,
        lastHandMs: Long,
        locked: Boolean,
    ): ModelPass? {
        val hands = lastHandMs <= 0L || nowMs - lastHandMs >= HAND_MS
        val objects = InferenceCadence.due(nowMs, lastObjectMs, locked)
        if (!hands && !objects) return null
        return ModelPass(objects = objects, hands = hands)
    }
}
