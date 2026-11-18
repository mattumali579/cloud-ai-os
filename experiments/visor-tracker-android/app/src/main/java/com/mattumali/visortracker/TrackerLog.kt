package com.mattumali.visortracker

import java.io.File

/**
 * On-device diagnostic log. Live View switches the phone Wi-Fi to the glasses,
 * which drops wireless ADB. This file stays on the phone and needs no USB.
 */
internal object TrackerLog {
    const val FILE_NAME = "visor-tracker-diagnostics.txt"
    private const val MAX_BYTES = 262_144
    private val gate = Any()

    fun record(directory: File, timestampMs: Long, stage: String, detail: String) {
        val clean = detail.replace("\n", " ").replace("\r", " ")
        append(directory, timestampMs, stage + " | " + clean)
    }

    fun append(directory: File, timestampMs: Long, line: String) {
        synchronized(gate) {
            try {
                if (!directory.exists() && !directory.mkdirs()) return
                val file = File(directory, FILE_NAME)
                file.appendText(timestampMs.toString() + " | " + line + "\n")
                if (file.length() > MAX_BYTES) {
                    val kept = file.readText().takeLast(MAX_BYTES / 2)
                    file.writeText(kept)
                }
            } catch (_: Exception) {
                // A full disk must not stop the camera loop.
            }
        }
    }

    fun read(directory: File): String {
        val file = File(directory, FILE_NAME)
        if (!file.isFile) return ""
        return try {
            file.readText()
        } catch (_: Exception) {
            ""
        }
    }
}
