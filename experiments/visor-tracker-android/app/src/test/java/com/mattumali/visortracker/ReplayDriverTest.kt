package com.mattumali.visortracker

import java.io.File
import org.junit.Assert.assertEquals
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

    @Test fun preservesDepthsAndScoresThroughSerialization() {
        val width = 720
        val height = 1280
        val points = (0 until 21).map { i ->
            (100f + i * 5f) to (200f + i * 8f)
        }
        val depths = (0 until 21).map { i ->
            if (i == 8) -60f else (i * -2f)
        }
        val originalBox = BoxObservation(
            kind = "hand",
            label = "hand",
            score = 0.83f,
            left = 100f,
            top = 200f,
            right = 250f,
            bottom = 400f,
            points = points,
            depths = depths,
        )
        val lineProtocol = buildString {
            appendLine("FRAME $width $height 1.0")
            appendLine("BOX hand \"hand\" 0.83 100.0 200.0 250.0 400.0")
            appendLine("POINTS " + points.joinToString(" ") { "${it.first} ${it.second}" })
            appendLine("DEPTHS " + depths.joinToString(" "))
            appendLine("END")
        }
        val parsed = parse(lineProtocol)
        assertEquals(1, parsed.size)
        val frame = parsed[0]
        assertEquals(width, frame.width)
        assertEquals(height, frame.height)
        assertEquals(1, frame.boxes.size)
        val parsedBox = frame.boxes[0]
        assertEquals(originalBox.kind, parsedBox.kind)
        assertEquals(originalBox.score, parsedBox.score, 1e-4f)
        assertEquals(originalBox.points.size, parsedBox.points.size)
        assertEquals(originalBox.depths.size, parsedBox.depths.size)
        assertEquals(21, parsedBox.depths.size)
        for (i in 0 until 21) {
            assertEquals("depth $i", originalBox.depths[i], parsedBox.depths[i], 1e-4f)
        }
        val tracker1 = ProductionTracker()
        val step1 = tracker1.step(listOf(originalBox), width, height)
        val tracker2 = ProductionTracker()
        val step2 = tracker2.step(listOf(parsedBox), width, height)
        assertEquals(step1.state, step2.state)
        assertEquals(step1.lockId, step2.lockId)
        assertEquals(step1.hitId, step2.hitId)
        assertEquals(step1.ray, step2.ray)
        assertEquals(step1.tipX, step2.tipX, 1e-3f)
        assertEquals(step1.tipY, step2.tipY, 1e-3f)
        assertEquals(step1.dirX, step2.dirX, 1e-3f)
        assertEquals(step1.dirY, step2.dirY, 1e-3f)
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
                "DEPTHS" -> {
                    val box = pending ?: error("DEPTHS before BOX")
                    val depths = parts.drop(1).map { it.toFloat() }
                    pending = box.copy(depths = depths)
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
