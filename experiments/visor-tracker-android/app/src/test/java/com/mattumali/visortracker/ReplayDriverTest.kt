package com.mattumali.visortracker

import java.io.File
import org.junit.Assume.assumeTrue
import org.junit.Test

/**
 * Reads detector boxes written by scripts/replay/run_replay.py and writes
 * the gesture, ray, and lock from [ProductionTracker]. This test is the
 * replay scorer. The Python file does not decide locks.
 *
 * The detection file is a line protocol, not org.json. Android's unit-test
 * org.json is a stub and returns null from put().
 */
class ReplayDriverTest {
    @Test fun writesProductionDecisions() {
        val dir = System.getenv("VISOR_REPLAY_DIR")
        assumeTrue("VISOR_REPLAY_DIR is unset; replay was not requested", !dir.isNullOrBlank())
        val folder = File(dir!!)
        val detections = File(folder, "detections.txt")
        check(detections.isFile) { "missing ${detections.absolutePath}" }
        val tracker = ProductionTracker()
        val frames = parse(detections.readText())
        val out = File(folder, "decisions.jsonl")
        out.bufferedWriter().use { writer ->
            for (frame in frames) {
                val step = tracker.step(frame.boxes, frame.width, frame.height)
                writer.append(decision(step))
                writer.newLine()
            }
        }
        check(out.readLines().count { it.isNotBlank() } == frames.size) {
            "decision count does not match detections"
        }
    }

    private data class Parsed(val width: Int, val height: Int, val boxes: List<BoxObservation>)

    private fun parse(text: String): List<Parsed> {
        val frames = mutableListOf<Parsed>()
        var width = 0
        var height = 0
        val boxes = mutableListOf<BoxObservation>()
        var pending: BoxObservation? = null
        fun finishBox() {
            val box = pending ?: return
            pending = null
            if (box.kind != "object") {
                boxes += box
                return
            }
            val chosen = LabelPolicy.selection(listOf(CategoryScore(box.label, box.score))) ?: return
            boxes += box.copy(label = chosen.name, score = chosen.score)
        }
        for (raw in text.lineSequence()) {
            val line = raw.trim()
            if (line.isEmpty()) continue
            val parts = line.split(' ')
            when (parts[0]) {
                "FRAME" -> {
                    finishBox()
                    if (width > 0) frames += Parsed(width, height, boxes.toList())
                    boxes.clear()
                    width = parts[1].toInt()
                    height = parts[2].toInt()
                }
                "BOX" -> {
                    finishBox()
                    pending = BoxObservation(
                        kind = parts[1],
                        label = unquote(parts[2]),
                        score = parts[3].toFloat(),
                        left = parts[4].toFloat(),
                        top = parts[5].toFloat(),
                        right = parts[6].toFloat(),
                        bottom = parts[7].toFloat(),
                    )
                }
                "POINTS" -> {
                    val box = pending ?: error("POINTS before BOX")
                    val numbers = parts.drop(1).map { it.toFloat() }
                    val points = numbers.chunked(2).map { it[0] to it[1] }
                    pending = box.copy(points = points)
                }
                "END" -> {
                    finishBox()
                    if (width > 0) frames += Parsed(width, height, boxes.toList())
                    boxes.clear()
                    width = 0
                    height = 0
                }
            }
        }
        return frames
    }

    private fun unquote(token: String): String {
        if (token.length >= 2 && token.startsWith("\"") && token.endsWith("\"")) {
            return token.substring(1, token.length - 1)
                .replace("\\u0020", " ")
                .replace("\\\"", "\"")
                .replace("\\\\", "\\")
        }
        return token
    }

    private fun decision(step: ProductionTracker.Step): String {
        val boxes = step.boxes.joinToString(",") { box ->
            val points = if (box.points.isEmpty()) {
                ""
            } else {
                val encoded = box.points.joinToString(",") { point ->
                    "[${point.first},${point.second}]"
                }
                ""","points":[$encoded]"""
            }
            """{"kind":${quote(box.kind)},"label":${quote(box.label)},"score":${box.score},"left":${box.left},"top":${box.top},"right":${box.right},"bottom":${box.bottom},"track_id":${box.trackId}$points}"""
        }
        return """{"state":${quote(step.state.name)},"lock_id":${jsonId(step.lockId)},"hit_id":${jsonId(step.hitId)},"ray":${step.ray},"tip_x":${step.tipX},"tip_y":${step.tipY},"dir_x":${step.dirX},"dir_y":${step.dirY},"reach":${step.reach},"boxes":[$boxes]}"""
    }

    private fun jsonId(id: Int?): String = id?.toString() ?: "null"

    private fun quote(value: String): String =
        "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
}
