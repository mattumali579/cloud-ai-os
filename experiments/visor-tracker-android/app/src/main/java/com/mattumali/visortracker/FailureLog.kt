package com.mattumali.visortracker

import java.io.File
import java.util.Locale

data class FailureBox(
    val id: Int,
    val label: String,
    val score: Float,
    val left: Float,
    val top: Float,
    val right: Float,
    val bottom: Float,
)

/**
 * One camera frame the tracker got wrong, or has not been checked yet.
 * [correctId] is set only by [FailureLog.labelLatest]. The selected id is the
 * tracker's choice and is never copied into the correct id.
 */
data class FailureExample(
    val frameTimestampMs: Long,
    val landmarks: List<Pair<Float, Float>>,
    val boxes: List<FailureBox>,
    val selectedId: Int?,
    val correctId: Int?,
    val category: String,
    val scores: Map<String, Float>,
)

/**
 * Local text file under the app files directory. Camera images are not written.
 */
class FailureLog(private val directory: File) {
    private val file = File(directory, FILE_NAME)
    private var latest: FailureExample? = null

    fun remember(frame: FrameDetections, selectedId: Int?) {
        val hands = frame.boxes.filter { it.kind == "hand" }
        val objects = frame.boxes.filter { it.kind == "object" && !it.held }
        val example = FailureExample(
            frameTimestampMs = frame.capturedAtMs,
            landmarks = hands.flatMap { it.points },
            boxes = objects.map { box ->
                FailureBox(box.trackId, box.label, box.score, box.left, box.top, box.right, box.bottom)
            },
            selectedId = selectedId,
            correctId = null,
            category = "unlabeled",
            scores = buildMap {
                hands.firstOrNull()?.let { put("hand", it.score) }
                objects.forEach { put("id${it.trackId}", it.score) }
            },
        )
        latest = example
        write(example)
    }

    /** Human label for the latest frame. Returns null when nothing has been seen. */
    fun labelLatest(correctId: Int, category: String = "labeled"): FailureExample? {
        val current = latest ?: read() ?: return null
        val labeled = current.copy(correctId = correctId, category = category)
        latest = labeled
        write(labeled)
        return labeled
    }

    fun read(): FailureExample? {
        if (!file.isFile) return null
        return decode(file.readText())
    }

    private fun write(example: FailureExample) {
        directory.mkdirs()
        file.writeText(encode(example))
    }

    companion object {
        const val FILE_NAME = "failure-example.txt"

        fun encode(example: FailureExample): String = buildString {
            appendLine("timestamp_ms=${example.frameTimestampMs}")
            appendLine("selected_id=${example.selectedId?.toString().orEmpty()}")
            appendLine("correct_id=${example.correctId?.toString().orEmpty()}")
            appendLine("category=${example.category}")
            appendLine("scores=" + example.scores.entries.joinToString(",") { (key, value) ->
                key + ":" + String.format(Locale.US, "%.4f", value)
            })
            appendLine("landmarks=" + example.landmarks.joinToString(",") { (x, y) ->
                String.format(Locale.US, "%.2f:%.2f", x, y)
            })
            appendLine("boxes=" + example.boxes.joinToString(";") { box ->
                listOf(
                    box.id.toString(),
                    box.label,
                    String.format(Locale.US, "%.4f", box.score),
                    String.format(Locale.US, "%.2f", box.left),
                    String.format(Locale.US, "%.2f", box.top),
                    String.format(Locale.US, "%.2f", box.right),
                    String.format(Locale.US, "%.2f", box.bottom),
                ).joinToString(":")
            })
        }

        fun decode(text: String): FailureExample {
            val lines = text.lineSequence().map { it.trim() }.filter { it.isNotEmpty() && "=" in it }
            val values = linkedMapOf<String, String>()
            for (line in lines) {
                val cut = line.indexOf('=')
                values[line.substring(0, cut)] = line.substring(cut + 1)
            }
            return FailureExample(
                frameTimestampMs = values["timestamp_ms"]?.toLongOrNull() ?: 0L,
                landmarks = parsePairs(values["landmarks"].orEmpty()),
                boxes = parseBoxes(values["boxes"].orEmpty()),
                selectedId = values["selected_id"]?.toIntOrNull(),
                correctId = values["correct_id"]?.toIntOrNull(),
                category = values["category"].orEmpty().ifBlank { "unlabeled" },
                scores = parseScores(values["scores"].orEmpty()),
            )
        }

        private fun parsePairs(text: String): List<Pair<Float, Float>> {
            if (text.isBlank()) return emptyList()
            return text.split(',').mapNotNull { part ->
                val cut = part.indexOf(':')
                if (cut <= 0) return@mapNotNull null
                val x = part.substring(0, cut).toFloatOrNull() ?: return@mapNotNull null
                val y = part.substring(cut + 1).toFloatOrNull() ?: return@mapNotNull null
                x to y
            }
        }

        private fun parseScores(text: String): Map<String, Float> {
            if (text.isBlank()) return emptyMap()
            val scores = linkedMapOf<String, Float>()
            for (part in text.split(',')) {
                val cut = part.indexOf(':')
                if (cut <= 0) continue
                val value = part.substring(cut + 1).toFloatOrNull() ?: continue
                scores[part.substring(0, cut)] = value
            }
            return scores
        }

        private fun parseBoxes(text: String): List<FailureBox> {
            if (text.isBlank()) return emptyList()
            return text.split(';').mapNotNull { part ->
                val bits = part.split(':')
                if (bits.size < 7) return@mapNotNull null
                FailureBox(
                    id = bits[0].toIntOrNull() ?: return@mapNotNull null,
                    label = bits[1],
                    score = bits[2].toFloatOrNull() ?: return@mapNotNull null,
                    left = bits[3].toFloatOrNull() ?: return@mapNotNull null,
                    top = bits[4].toFloatOrNull() ?: return@mapNotNull null,
                    right = bits[5].toFloatOrNull() ?: return@mapNotNull null,
                    bottom = bits[6].toFloatOrNull() ?: return@mapNotNull null,
                )
            }
        }
    }
}
