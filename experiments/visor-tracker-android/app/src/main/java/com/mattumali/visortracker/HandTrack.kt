package com.mattumali.visortracker

import kotlin.math.hypot
import kotlin.math.max

/**
 * Whole-hand identity uses the MediaPipe index fingertip as the primary
 * reference. Bounding-box overlap alone drops a fast-moving hand and lets
 * EfficientDet treat that region as a generic object.
 */
object HandTrack {
    const val HAND_GRACE_FRAMES = 6

    fun indexTip(hand: BoxObservation): Pair<Float, Float>? {
        if (hand.kind != "hand") return null
        val tip = hand.points.getOrNull(IndexPoint.INDEX_TIP) ?: return null
        if (!tip.first.isFinite() || !tip.second.isFinite()) return null
        return tip
    }

    fun forDisplay(boxes: List<BoxObservation>): List<BoxObservation> {
        val live = boxes.filter { box ->
            box.kind == "hand" && !box.held && paintable(box)
        }
        if (live.isNotEmpty()) return live.sortedByDescending { it.score }
        return boxes.filter { box -> box.kind == "hand" && paintable(box) }
            .sortedByDescending { it.score }
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
        val limit = span * 1.75f
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
