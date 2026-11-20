package com.mattumali.visortracker

/**
 * Tries a few lock and schedule numbers on labeled synthetic frames.
 * A candidate is kept only when its score is strictly higher. This does not
 * train or edit a neural network. There is no labeled image set and no training run.
 */
data class TuneConfig(
    val minHandScore: Float = IndexPoint.MIN_SCORE,
    val rayHysteresis: Float = IndexPoint.RAY_HYSTERESIS,
    val dwellFrames: Int = 2,
    val lostFrames: Int = 3,
    val knownReplacementFrames: Int = 2,
    val objectEveryLocked: Int = 1,
    val releaseFrames: Int = GestureGate.DEFAULT_RELEASE_FRAMES,
)

data class TuneOutcome(
    val chosen: TuneConfig,
    val startScore: Int,
    val score: Int,
    val accepted: List<String>,
    val rejected: List<String>,
)

object TrackerTuner {
    fun tune(start: TuneConfig = TuneConfig(knownReplacementFrames = 4), cases: List<TuneCase> = standardCases()): TuneOutcome {
        var chosen = start
        var best = score(chosen, cases)
        val startScore = best
        val accepted = mutableListOf<String>()
        val rejected = mutableListOf<String>()
        for ((name, edit) in trialEdits()) {
            val candidate = edit(chosen)
            val next = score(candidate, cases)
            val note = name + " " + next + " vs " + best
            if (next > best) {
                chosen = candidate
                best = next
                accepted += note
            } else {
                rejected += note
            }
        }
        return TuneOutcome(chosen, startScore, best, accepted, rejected)
    }

    fun score(config: TuneConfig, cases: List<TuneCase> = standardCases()): Int {
        var total = 0
        for (case in cases) {
            if (passes(config, case)) total += 10
        }
        return total
    }

    private fun passes(config: TuneConfig, case: TuneCase): Boolean {
        val aim = AimLock(
            acquireFrames = config.dwellFrames,
            switchFrames = config.dwellFrames,
            lostFramesLimit = config.lostFrames,
            knownReplacementFrames = config.knownReplacementFrames,
            minHandScore = config.minHandScore,
            rayHysteresis = config.rayHysteresis,
            releaseFrames = config.releaseFrames,
        )
        var locked = false
        case.frames.forEachIndexed { index, frame ->
            val before = aim.lockedId
            val decision = aim.observe(frame)
            if (case.forbidSwitchBefore != null && index < case.forbidSwitchBefore &&
                decision.changed && before != null
            ) {
                return false
            }
            if (index >= case.readyAt && aim.lockedId == case.correctId) locked = true
            if (index == case.decidedBy && aim.lockedId != case.correctId) return false
            if (case.holdAfter && locked && aim.lockedId != case.correctId) return false
        }
        return aim.lockedId == case.correctId
    }

    private fun trialEdits(): List<Pair<String, (TuneConfig) -> TuneConfig>> = listOf(
        "knownReplacementFrames=2" to { it.copy(knownReplacementFrames = 2) },
        "minHandScore=0.99" to { it.copy(minHandScore = 0.99f) },
        "dwellFrames=1" to { it.copy(dwellFrames = 1) },
        "lostFrames=1" to { it.copy(lostFrames = 1) },
        "rayHysteresis=1.01" to { it.copy(rayHysteresis = 1.01f) },
        "objectEveryLocked=3" to { it.copy(objectEveryLocked = 3) },
        "releaseFrames=1" to { it.copy(releaseFrames = 1) },
        "releaseFrames=8" to { it.copy(releaseFrames = 8) },
    )

    data class TuneCase(
        val name: String,
        val frames: List<FrameDetections>,
        val correctId: Int?,
        val decidedBy: Int,
        val readyAt: Int = 0,
        val holdAfter: Boolean = false,
        val forbidSwitchBefore: Int? = null,
    )

    fun standardCases(): List<TuneCase> {
        val pointed = objectBox(2, 120f, 30f, 170f, 70f, 0.8f)
        val center = objectBox(1, 70f, 70f, 130f, 130f, 0.95f)
        val picture = frame(center, pointed, syntheticHand(90f, 50f))
        val one = frame(pointed, syntheticHand(90f, 50f))
        val farther = objectBox(4, 130f, 30f, 180f, 70f, 0.7f)
        val nearer = objectBox(5, 120f, 30f, 150f, 70f, 0.9f)
        return listOf(
            TuneCase("one finger one object", listOf(one, one), 2, decidedBy = 1),
            TuneCase(
                "ray not center",
                listOf(picture, picture),
                2,
                decidedBy = 1,
                holdAfter = true,
            ),
            TuneCase(
                "one frame does not switch",
                listOf(
                    picture,
                    picture,
                    frame(center, pointed, objectBox(9, 120f, 120f, 170f, 160f, 0.8f), syntheticHand(90f, 140f)),
                ),
                2,
                decidedBy = 2,
                forbidSwitchBefore = 3,
            ),
            TuneCase(
                "a disappeared point is not replaced by the center object",
                listOf(picture, picture, frame(center), frame(center)),
                2,
                decidedBy = 3,
            ),
            TuneCase(
                "occlusion hold when nothing else is visible",
                listOf(picture, picture, frame(), frame(), picture),
                2,
                decidedBy = 4,
                readyAt = 1,
                holdAfter = true,
            ),
            TuneCase(
                "one closed hand does not release",
                listOf(picture, picture, frame(pointed, center, syntheticFist())),
                2,
                decidedBy = 2,
                readyAt = 1,
                holdAfter = true,
            ),
            TuneCase(
                "stable closed hand releases and does not switch",
                listOf(
                    picture,
                    picture,
                    frame(pointed, center, syntheticFist()),
                    frame(pointed, center, syntheticFist()),
                    frame(pointed, center, syntheticFist()),
                    frame(pointed, center, syntheticFist()),
                ),
                null,
                decidedBy = 5,
                forbidSwitchBefore = 5,
            ),
            TuneCase(
                "hysteresis keeps the current ray hit",
                listOf(
                    frame(farther, syntheticHand(90f, 50f)),
                    frame(farther, syntheticHand(90f, 50f)),
                    frame(nearer, farther, syntheticHand(90f, 50f)),
                    frame(nearer, farther, syntheticHand(90f, 50f)),
                ),
                4,
                decidedBy = 3,
            ),
        )
    }

    private fun frame(vararg boxes: BoxObservation) = FrameDetections(200, 200, boxes.toList())

    private fun objectBox(id: Int, left: Float, top: Float, right: Float, bottom: Float, score: Float) =
        BoxObservation("object", "object", score, left, top, right, bottom, trackId = id)

    /** Extended index, other fingers curled. Same geometry as the unit fixtures. */
    private fun syntheticHand(tipX: Float, tipY: Float): BoxObservation {
        val points = MutableList(21) { 0f to tipY }
        points[IndexPoint.WRIST] = tipX - 80f to tipY
        points[IndexPoint.INDEX_MCP] = tipX - 60f to tipY
        points[IndexPoint.INDEX_PIP] = tipX - 40f to tipY
        points[IndexPoint.INDEX_DIP] = tipX - 20f to tipY
        points[IndexPoint.INDEX_TIP] = tipX to tipY
        points[9] = tipX - 58f to tipY + 14f
        points[10] = tipX - 52f to tipY + 20f
        points[11] = tipX - 50f to tipY + 18f
        points[12] = tipX - 54f to tipY + 16f
        points[13] = tipX - 64f to tipY + 18f
        points[14] = tipX - 60f to tipY + 24f
        points[15] = tipX - 58f to tipY + 22f
        points[16] = tipX - 62f to tipY + 20f
        points[17] = tipX - 70f to tipY + 22f
        points[18] = tipX - 68f to tipY + 28f
        points[19] = tipX - 66f to tipY + 26f
        points[20] = tipX - 70f to tipY + 24f
        points[1] = tipX - 74f to tipY - 8f
        points[2] = tipX - 68f to tipY - 4f
        points[3] = tipX - 64f to tipY
        points[4] = tipX - 66f to tipY + 4f
        return BoxObservation(
            "hand", "hand", 0.92f,
            tipX - 80f, tipY - 10f, tipX, tipY + 30f,
            points = points, trackId = 50,
        )
    }

    /** Curled index and the other fingertips near the palm. Not a pointing ray. */
    private fun syntheticFist(): BoxObservation {
        val points = MutableList(21) { 80f to 100f }
        points[IndexPoint.WRIST] = 70f to 100f
        points[IndexPoint.INDEX_MCP] = 90f to 100f
        points[IndexPoint.INDEX_PIP] = 84f to 108f
        points[IndexPoint.INDEX_DIP] = 80f to 106f
        points[IndexPoint.INDEX_TIP] = 78f to 104f
        points[12] = 86f to 110f
        points[16] = 74f to 110f
        points[20] = 68f to 108f
        return BoxObservation(
            "hand", "hand", 0.92f,
            60f, 90f, 100f, 120f,
            points = points, trackId = 50,
        )
    }
}
