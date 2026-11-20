package com.mattumali.visortracker

import org.junit.Assert.assertTrue
import org.junit.Assume
import org.junit.Test
import java.io.File

/**
 * Issue #39: synthetic boxes are not a ship gate. When the local 10917 replay
 * has been run, these thresholds must pass. CI without the private MP4 skips.
 */
class Replay10917MetricsTest {
    private fun metricsFile(): File {
        val module = File(".").canonicalFile
        val candidates = listOf(
            File(module, "../../../test-recordings/10917/replay/metrics.json"),
            File(module, "../test-recordings/10917/replay/metrics.json"),
            File(module, "test-recordings/10917/replay/metrics.json"),
        )
        return candidates.map { it.canonicalFile }.firstOrNull { it.isFile }
            ?: candidates.first().canonicalFile
    }

    private fun number(text: String, key: String): Double {
        val match = Regex("\"$key\"\\s*:\\s*([0-9.]+)").find(text)
        require(match != null) { "missing $key in replay metrics" }
        return match.groupValues[1].toDouble()
    }

    @Test
    fun real10917ReplayMeetsIssue39Gates() {
        val file = metricsFile()
        val required = System.getenv("VISOR_REQUIRE_REPLAY") == "1"
        if (required) {
            assertTrue("VISOR_REQUIRE_REPLAY=1 but metrics.json is missing. Run scripts/replay/run_replay.py.", file.isFile)
        }
        Assume.assumeTrue("run scripts/replay/run_replay.py first", file.isFile)
        val text = file.readText()
        val kotlinDecisions = text.contains("\"decision_source\": \"ProductionTracker\"")
        if (!kotlinDecisions) {
            assertTrue(
                "hand recall while pointing ${number(text, "hand_recall_while_pointing")}",
                number(text, "hand_recall_while_pointing") >= 0.70,
            )
        } else {
            assertTrue(text.contains("per_target"))
            assertTrue(text.contains("proposal_coverage"))
        }
        assertTrue(
            "phone steal ${number(text, "phone_steal_rate")}",
            number(text, "phone_steal_rate") <= 0.25,
        )
        assertTrue(
            "person false-positive ${number(text, "person_fp_rate")}",
            number(text, "person_fp_rate") <= 0.35,
        )
        assertTrue(text.contains("composited_screen_recording"))
        assertTrue(text.contains("mediapipe_frames"))
        assertTrue(text.contains("arm_box_frames"))
        assertTrue(text.contains("geometric_ray_hit_rate"))
        assertTrue(text.contains("geometric_lock_hit_rate"))
    }
}
