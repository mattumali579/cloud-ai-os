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
