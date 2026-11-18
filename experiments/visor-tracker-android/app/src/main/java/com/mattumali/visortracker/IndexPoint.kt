package com.mattumali.visortracker

import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * A 2D ray on the camera image, from the index fingertip along the finger.
 * This is not a 3D direction. Two objects on the same ray cannot be separated
 * by depth; the nearer hit along the image ray wins, and the current lock
 * stays when it is also on that ray.
 */
data class PointingRay(
    val tipX: Float,
    val tipY: Float,
    val dirX: Float,
    val dirY: Float,
    val reach: Float,
    val joints: List<Pair<Float, Float>>,
)

/**
 * MediaPipe Hand Landmarker indices. The points list on a hand box is that
 * model's 21 landmarks in this order, in the same pixel space as object boxes.
 */
object IndexPoint {
    const val WRIST = 0
    const val INDEX_MCP = 5
    const val INDEX_PIP = 6
    const val INDEX_DIP = 7
    const val INDEX_TIP = 8
    const val MIN_SCORE = 0.40f
    const val LIMITATION =
        "Finger pointing is a 2D ray on the camera image, not a measured 3D aim. " +
            "Overlapping objects along that ray stay ambiguous."

    private val OTHER_TIPS = intArrayOf(12, 16, 20)

    fun ray(frame: FrameDetections): PointingRay? {
        if (frame.width <= 0 || frame.height <= 0) return null
        val hands = frame.boxes.filter { it.kind == "hand" && !it.held && it.score >= MIN_SCORE }
        return hands.mapNotNull { extended(it, frame.width, frame.height) }
            .maxByOrNull { it.reach }
    }

    fun extended(hand: BoxObservation, frameWidth: Int, frameHeight: Int): PointingRay? {
        val points = hand.points
        if (points.size < 21 || hand.score < MIN_SCORE) return null
        val wrist = points[WRIST]
        val mcp = points[INDEX_MCP]
        val pip = points[INDEX_PIP]
        val dip = points[INDEX_DIP]
        val tip = points[INDEX_TIP]
        val joints = listOf(wrist, mcp, pip, dip, tip)
        if (joints.any { !it.first.isFinite() || !it.second.isFinite() }) return null
        if (joints.any { it.first < -4f || it.second < -4f || it.first > frameWidth + 4f || it.second > frameHeight + 4f }) {
            return null
        }
        val palm = distance(mcp, wrist)
        val indexLength = distance(tip, mcp)
        if (palm < 4f || indexLength < palm * 0.85f) return null
        val straight = cosine(mcp, pip, tip)
        if (straight > -0.50f) return null
        val tipReach = distance(tip, wrist)
        for (other in OTHER_TIPS) {
            if (distance(points[other], wrist) > tipReach * 0.92f) return null
        }
        val dx = tip.first - dip.first
        val dy = tip.second - dip.second
        val length = hypot(dx, dy)
        if (length < 3f) return null
        val reach = hypot(frameWidth.toFloat(), frameHeight.toFloat())
        return PointingRay(tip.first, tip.second, dx / length, dy / length, reach, joints)
    }

    /**
     * First object the ray enters. The locked object wins when it is also on
     * the ray and not much farther from the fingertip than the closest hit.
     */
    fun alongRay(
        ray: PointingRay,
        objects: List<BoxObservation>,
        preferId: Int?,
    ): BoxObservation? {
        val hits = objects.mapNotNull { box ->
            if (!pointable(box)) return@mapNotNull null
            val distance = entered(ray, box) ?: return@mapNotNull null
            box to distance
        }
        if (hits.isEmpty()) return null
        val closest = hits.minWith(compareBy<Pair<BoxObservation, Float>> { it.second }.thenBy { it.first.trackId })
        val preferred = hits.firstOrNull { it.first.trackId == preferId }
        if (preferred != null && preferred.second <= closest.second * RAY_HYSTERESIS + 8f) return preferred.first
        return closest.first
    }

    /** Next object after the locked one leaves. Highest score, then largest box, then lowest id. */
    fun replacement(objects: List<BoxObservation>, excluding: Int?): BoxObservation? {
        val rest = objects.filter { pointable(it) && it.trackId != excluding }
        if (rest.isEmpty()) return null
        return rest.minWith(
            compareByDescending<BoxObservation> { it.score }
                .thenByDescending { it.area() }
                .thenBy { it.trackId }
        )
    }

    fun entered(ray: PointingRay, box: BoxObservation): Float? {
        var tMin = 0f
        var tMax = ray.reach
        val origin = floatArrayOf(ray.tipX, ray.tipY)
        val direction = floatArrayOf(ray.dirX, ray.dirY)
        val min = floatArrayOf(box.left, box.top)
        val max = floatArrayOf(box.right, box.bottom)
        for (axis in 0..1) {
            if (kotlin.math.abs(direction[axis]) < 1e-4f) {
                if (origin[axis] < min[axis] || origin[axis] > max[axis]) return null
            } else {
                var near = (min[axis] - origin[axis]) / direction[axis]
                var far = (max[axis] - origin[axis]) / direction[axis]
                if (near > far) {
                    val swap = near
                    near = far
                    far = swap
                }
                tMin = max(tMin, near)
                tMax = min(tMax, far)
                if (tMin > tMax) return null
            }
        }
        return tMin
    }

    private fun pointable(box: BoxObservation): Boolean =
        !box.held && box.kind == "object" && box.trackId > 0 && box.right > box.left && box.bottom > box.top

    private fun distance(a: Pair<Float, Float>, b: Pair<Float, Float>): Float =
        hypot(a.first - b.first, a.second - b.second)

    /** Cosine of the angle at [joint]. A straight finger is near -1. */
    private fun cosine(
        back: Pair<Float, Float>,
        joint: Pair<Float, Float>,
        forward: Pair<Float, Float>,
    ): Float {
        val ax = back.first - joint.first
        val ay = back.second - joint.second
        val bx = forward.first - joint.first
        val by = forward.second - joint.second
        val denom = hypot(ax, ay) * hypot(bx, by)
        if (denom < 1e-3f) return 1f
        return (ax * bx + ay * by) / denom
    }

    private const val RAY_HYSTERESIS = 1.35f
}
