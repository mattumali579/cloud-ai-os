package com.mattumali.visortracker

/**
 * One sticky error per pipeline stage.
 * A later video frame does not clear a detector or grayscale fault.
 */
class StageFaults {
    private val faults = linkedMapOf<String, String>()

    @Synchronized fun fail(stage: String, message: String) {
        faults[stage] = message
    }

    @Synchronized fun clear(stage: String) {
        faults.remove(stage)
    }

    @Synchronized fun summary(): String =
        faults.entries.joinToString("; ") { "${it.key}: ${it.value}" }
}
