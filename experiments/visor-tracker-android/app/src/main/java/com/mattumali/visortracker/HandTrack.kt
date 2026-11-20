package com.mattumali.visortracker

import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * Whole-hand identity uses the MediaPipe index fingertip as the primary
 * reference. Bounding-box overlap alone drops a fast-moving hand and lets
 * EfficientDet treat that region as a generic object.
 */
object HandTrack {
    const val HAND_GRACE_FRAMES = 4

    fun indexTip(hand: BoxObservation): Pair<Float, Float>? {
        if (hand.kind != "hand") return null
        val tip = hand.points.getOrNull(IndexPoint.INDEX_TIP) ?: return null
        if (!tip.first.isFinite() || !tip.second.isFinite()) return null
        return tip
    }

    /**
     * When MediaPipe drops a side-on point, EfficientDet still has the arm.
     * Slide the last skeleton so the wrist stays inside that box instead of
     * freezing at the last palm pose.
     */
    /**
     * 10917: MediaPipe IMAGE mode misses most side-on points. Build an index
     * chain along the forearm box from the frame-edge entry to the far corner.
     */
    fun fromArmBox(arm: BoxObservation, frameWidth: Int, frameHeight: Int): BoxObservation {
        val margin = max(12f, kotlin.math.min(frameWidth, frameHeight) * 0.03f)
        val fromRight = frameWidth - arm.right <= margin
        val fromLeft = arm.left <= margin
        val fromBottom = frameHeight - arm.bottom <= margin
        val maxSpan = min(arm.right - arm.left, frameWidth * 0.38f)
        val left = if (fromRight) max(arm.left, arm.right - maxSpan) else arm.left
        val right = if (fromLeft && !fromRight) min(arm.right, arm.left + maxSpan) else arm.right
        val midX = (left + right) * 0.5f
        val midY = (arm.top + arm.bottom) * 0.5f
        val clipped = arm.copy(left = left, right = right)
        val wrist = when {
            fromRight && (!fromLeft || clipped.right - midX >= midX - clipped.left) ->
                clipped.right to midY
            fromLeft -> clipped.left to midY
            fromBottom -> midX to clipped.bottom
            else -> midX to midY
        }
        // Aim across the table, toward the middle of the frame. The top of the
        // arm box is usually the chair or the wall, not the fingertip.
        val tableY = (frameHeight * 0.55f).coerceIn(clipped.top, clipped.bottom)
        val tip = when {
            fromRight -> clipped.left to tableY
            fromLeft -> clipped.right to tableY
            else -> midX to clipped.top
        }
        fun along(t: Float) = wrist.first + (tip.first - wrist.first) * t to
            wrist.second + (tip.second - wrist.second) * t
        val mcp = along(0.35f)
        val points = MutableList(21) { mcp }
        points[IndexPoint.WRIST] = along(0f)
        points[IndexPoint.INDEX_MCP] = mcp
        points[IndexPoint.INDEX_PIP] = along(0.55f)
        points[IndexPoint.INDEX_DIP] = along(0.75f)
        points[IndexPoint.INDEX_TIP] = along(0.95f)
        points[9] = along(0.38f)
        points[12] = along(0.42f)
        points[13] = along(0.36f)
        points[16] = along(0.40f)
        points[17] = along(0.33f)
        points[20] = along(0.37f)
        val xs = points.map { it.first }
        val ys = points.map { it.second }
        return BoxObservation(
            kind = "hand",
            label = "pointing hand",
            score = 0.55f,
            left = xs.minOrNull() ?: arm.left,
            top = ys.minOrNull() ?: arm.top,
            right = xs.maxOrNull() ?: arm.right,
            bottom = ys.maxOrNull() ?: arm.bottom,
            points = points,
            trackId = 0,
            synthetic = true,
        )
    }

    fun pinToArm(hand: BoxObservation, arm: BoxObservation): BoxObservation {
        val wrist = hand.points.getOrNull(IndexPoint.WRIST) ?: return hand
        val x = wrist.first.coerceIn(arm.left, arm.right)
        val y = wrist.second.coerceIn(arm.top, arm.bottom)
        val dx = x - wrist.first
        val dy = y - wrist.second
        if (dx == 0f && dy == 0f) return hand
        return hand.copy(
            left = hand.left + dx,
            top = hand.top + dy,
            right = hand.right + dx,
            bottom = hand.bottom + dy,
            points = hand.points.map { it.first + dx to it.second + dy },
        )
    }

    fun forDisplay(boxes: List<BoxObservation>): List<BoxObservation> {
        val live = boxes.filter { box ->
            box.kind == "hand" && !box.held && paintable(box)
        }
        return live.sortedByDescending { it.score }
    }

    fun objectsOnly(boxes: List<BoxObservation>): List<BoxObservation> =
        boxes.filter { it.kind == "object" }

    fun compose(objects: List<BoxObservation>, hands: List<BoxObservation>): List<BoxObservation> =
        objects + hands

    /**
     * Higher is a better match. Zero means the detections are different hands.
     * Index-tip distance is the main signal; wrist distance is the fallback.
     */
    fun matchScore(previous: BoxObservation, next: BoxObservation): Float {
        if (previous.kind != "hand" || next.kind != "hand") return 0f
        val span = max(
            hypot(previous.right - previous.left, previous.bottom - previous.top),
            hypot(next.right - next.left, next.bottom - next.top),
        ).coerceAtLeast(48f)
        val limit = span * 2.2f
        val previousTip = indexTip(previous)
        val nextTip = indexTip(next)
        if (previousTip != null && nextTip != null) {
            val distance = hypot(previousTip.first - nextTip.first, previousTip.second - nextTip.second)
            if (distance <= limit) return 2f - distance / limit
        }
        val previousWrist = previous.points.getOrNull(IndexPoint.WRIST)
        val nextWrist = next.points.getOrNull(IndexPoint.WRIST)
        if (previousWrist != null && nextWrist != null) {
            val distance = hypot(previousWrist.first - nextWrist.first, previousWrist.second - nextWrist.second)
            if (distance <= limit) return 1.1f - distance / limit
        }
        return 0f
    }

    private fun paintable(box: BoxObservation): Boolean =
        box.points.size >= 21 && box.trackId > 0 && box.right > box.left && box.bottom > box.top
}

/** Convex outline of the 21 landmarks so the overlay traces the whole hand. */
object HandOutline {
    fun hull(points: List<Pair<Float, Float>>): List<Pair<Float, Float>> {
        val unique = points.filter { it.first.isFinite() && it.second.isFinite() }
            .distinctBy { "${it.first},${it.second}" }
        if (unique.size < 3) return unique
        val sorted = unique.sortedWith(compareBy({ it.first }, { it.second }))
        val lower = mutableListOf<Pair<Float, Float>>()
        for (point in sorted) {
            while (lower.size >= 2 && cross(lower[lower.size - 2], lower.last(), point) <= 0f) {
                lower.removeAt(lower.lastIndex)
            }
            lower += point
        }
        val upper = mutableListOf<Pair<Float, Float>>()
        for (point in sorted.asReversed()) {
            while (upper.size >= 2 && cross(upper[upper.size - 2], upper.last(), point) <= 0f) {
                upper.removeAt(upper.lastIndex)
            }
            upper += point
        }
        return (lower.dropLast(1) + upper.dropLast(1)).ifEmpty { unique }
    }

    private fun cross(
        origin: Pair<Float, Float>,
        a: Pair<Float, Float>,
        b: Pair<Float, Float>,
    ): Float = (a.first - origin.first) * (b.second - origin.second) -
        (a.second - origin.second) * (b.first - origin.first)
}
