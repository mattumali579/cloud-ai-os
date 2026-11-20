package com.mattumali.visortracker

import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * A projected ray on the camera image. MediaPipe z is used to recognize a
 * foreshortened finger, but object boxes still have no measured depth. When
 * the finger is aimed mostly through the image plane, [depthDominant] changes
 * selection to a fingertip cursor instead of inventing an unstable 2D ray.
 */
data class PointingRay(
    val tipX: Float,
    val tipY: Float,
    val dirX: Float,
    val dirY: Float,
    val reach: Float,
    val joints: List<Pair<Float, Float>>,
    val depthDominant: Boolean = false,
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
    const val MIN_SCORE = 0.28f
    const val MAX_POINTABLE_FRAME_FRACTION = 0.20f
    const val LIMITATION =
        "Hand z recognizes depth-angled pointing, but object boxes have no measured depth. " +
            "Depth-angled aim uses the fingertip position; overlapping objects stay ambiguous."

    private val OTHER_TIPS = intArrayOf(12, 16, 20)
    private val FINGERS = listOf(
        Finger(INDEX_TIP, INDEX_MCP, INDEX_PIP, INDEX_DIP),
        Finger(12, 9, 10, 11),
        Finger(16, 13, 14, 15),
        Finger(20, 17, 18, 19),
    )

    fun ray(frame: FrameDetections, minScore: Float = MIN_SCORE): PointingRay? {
        if (frame.width <= 0 || frame.height <= 0) return null
        val hands = frame.boxes.filter { it.kind == "hand" && !it.held && it.score >= minScore }
        return hands.mapNotNull { extended(it, frame.width, frame.height, minScore) }
            .maxByOrNull { it.reach }
    }

    fun extended(
        hand: BoxObservation,
        frameWidth: Int,
        frameHeight: Int,
        minScore: Float = MIN_SCORE,
    ): PointingRay? {
        val points = hand.points
        if (points.size < 21 || hand.score < minScore) return null
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
        val hasDepth = hand.depths.size >= 21 && hand.depths.take(21).all { it.isFinite() }
        val palm = distance3(hand, INDEX_MCP, WRIST, hasDepth)
        if (palm < 4f) return null
        val ranked = FINGERS.map { finger ->
            RankedFinger(
                finger = finger,
                reach = distance3(hand, finger.tip, WRIST, hasDepth),
                length = distance3(hand, finger.tip, finger.mcp, hasDepth),
                straight = cosine3(hand, finger.mcp, finger.pip, finger.tip, hasDepth),
            )
        }.sortedByDescending { it.reach }
        val best = ranked.first()
        val secondReach = ranked.getOrNull(1)?.reach ?: 0f
        val index = ranked.first { it.finger.tip == INDEX_TIP }
        val indexUsable = index.length >= palm * 0.70f && index.straight <= -0.25f &&
            index.reach >= secondReach * 1.05f && index.reach >= palm * 0.70f &&
            index.finger.tip == INDEX_TIP && best.finger.tip == INDEX_TIP
        if (!indexUsable) return null
        val chosenTip = tip
        val chosenDip = dip
        val chosenJoints = joints
        val dx = chosenTip.first - chosenDip.first
        val dy = chosenTip.second - chosenDip.second
        val projectedLength = hypot(dx, dy)
        val dz = if (hasDepth) hand.depths[INDEX_TIP] - hand.depths[INDEX_DIP] else 0f
        val segmentLength = kotlin.math.sqrt(dx * dx + dy * dy + dz * dz)
        if (segmentLength < 3f) return null
        val depthDominant = hasDepth && kotlin.math.abs(dz) > projectedLength * 0.75f
        if (!depthDominant && projectedLength < 3f) return null
        val reach = hypot(frameWidth.toFloat(), frameHeight.toFloat())
        val dirX = if (projectedLength >= 3f) dx / projectedLength else 0f
        val dirY = if (projectedLength >= 3f) dy / projectedLength else 0f
        return PointingRay(chosenTip.first, chosenTip.second, dirX, dirY, reach, chosenJoints, depthDominant)
    }

    /**
     * First object the ray enters. The locked object wins when it is also on
     * the ray and not much farther from the fingertip than the closest hit.
     */
    fun alongRay(
        ray: PointingRay,
        objects: List<BoxObservation>,
        preferId: Int?,
        hysteresis: Float = RAY_HYSTERESIS,
        frameWidth: Int = 0,
        frameHeight: Int = 0,
    ): BoxObservation? {
        if (ray.depthDominant) return underFingertip(ray, objects, preferId, frameWidth, frameHeight)
        val hits = objects.mapNotNull { box ->
            if (!pointable(box, frameWidth, frameHeight)) return@mapNotNull null
            val distance = entered(ray, box) ?: return@mapNotNull null
            box to distance
        }
        if (hits.isEmpty()) return null
        val closest = hits.minWith(compareBy<Pair<BoxObservation, Float>> { it.second }.thenBy { it.first.trackId })
        val preferred = hits.firstOrNull { it.first.trackId == preferId }
        if (preferred != null && preferred.second <= closest.second * hysteresis + 8f) return preferred.first
        return closest.first
    }

    /**
     * A camera cannot recover an object box's distance. For a depth-angled
     * finger, use the projected fingertip as a stable cursor and require it to
     * be inside or close to the target. This fixes foreshortening without
     * pretending the monocular detector can distinguish overlapping depths.
     */
    private fun underFingertip(
        ray: PointingRay,
        objects: List<BoxObservation>,
        preferId: Int?,
        frameWidth: Int = 0,
        frameHeight: Int = 0,
    ): BoxObservation? {
        val radius = max(24f, ray.reach * 0.045f)
        val hits = objects.mapNotNull { box ->
            if (!pointable(box, frameWidth, frameHeight)) return@mapNotNull null
            val dx = when {
                ray.tipX < box.left -> box.left - ray.tipX
                ray.tipX > box.right -> ray.tipX - box.right
                else -> 0f
            }
            val dy = when {
                ray.tipY < box.top -> box.top - ray.tipY
                ray.tipY > box.bottom -> ray.tipY - box.bottom
                else -> 0f
            }
            val distance = hypot(dx, dy)
            if (distance <= radius) box to distance else null
        }
        if (hits.isEmpty()) return null
        return hits.firstOrNull { it.first.trackId == preferId }?.first
            ?: hits.minWith(compareBy<Pair<BoxObservation, Float>> { it.second }.thenByDescending { it.first.score }).first
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
        // Fingertip inside this box is a self-hit (arm labeled person, hand
        // in front of a couch). 10912 locked those instead of the far object.
        if (ray.tipX >= box.left && ray.tipX <= box.right &&
            ray.tipY >= box.top && ray.tipY <= box.bottom
        ) {
            return null
        }
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

    /**
     * Index folded back toward the palm. [extended] already rejects this.
     * A single curled frame is not a command.
     */
    fun curledIndex(hand: BoxObservation): Boolean {
        val points = hand.points
        if (points.size < 21) return false
        val palm = distance(points[INDEX_MCP], points[WRIST])
        if (palm < 4f) return false
        return distance(points[INDEX_TIP], points[INDEX_MCP]) < palm * 0.85f
    }

    /** All four fingertips stay near the palm. */
    fun fist(hand: BoxObservation): Boolean {
        val points = hand.points
        if (points.size < 21) return false
        val wrist = points[WRIST]
        val palm = distance(points[INDEX_MCP], wrist)
        if (palm < 4f) return false
        val tips = intArrayOf(INDEX_TIP, 12, 16, 20)
        return tips.all { distance(points[it], wrist) <= palm * 1.35f }
    }

    /**
     * Closed fist or a fully curled index. An extended index is never this.
     * [GestureGate] may release a lock only after the same gesture repeats.
     */
    fun releaseGesture(hand: BoxObservation, frameWidth: Int, frameHeight: Int): Boolean {
        if (hand.points.size < 21 || hand.score < MIN_SCORE) return false
        if (extended(hand, frameWidth, frameHeight) != null) return false
        return curledIndex(hand) || fist(hand)
    }

    private fun pointable(box: BoxObservation, frameWidth: Int = 0, frameHeight: Int = 0): Boolean {
        if (box.held || box.kind != "object" || box.trackId <= 0 || box.right <= box.left || box.bottom <= box.top) {
            return false
        }
        if (frameWidth > 0 && frameHeight > 0) {
            val limit = frameWidth.toFloat() * frameHeight.toFloat() * MAX_POINTABLE_FRAME_FRACTION
            if (box.area() > limit) return false
        }
        return true
    }

    private data class Finger(val tip: Int, val mcp: Int, val pip: Int, val dip: Int)
    private data class RankedFinger(val finger: Finger, val reach: Float, val length: Float, val straight: Float)

    private fun distance(a: Pair<Float, Float>, b: Pair<Float, Float>): Float =
        hypot(a.first - b.first, a.second - b.second)

    private fun distance3(hand: BoxObservation, a: Int, b: Int, hasDepth: Boolean): Float {
        val dx = hand.points[a].first - hand.points[b].first
        val dy = hand.points[a].second - hand.points[b].second
        val dz = if (hasDepth) hand.depths[a] - hand.depths[b] else 0f
        return kotlin.math.sqrt(dx * dx + dy * dy + dz * dz)
    }

    private fun cosine3(
        hand: BoxObservation,
        back: Int,
        joint: Int,
        forward: Int,
        hasDepth: Boolean,
    ): Float {
        if (!hasDepth) return cosine(hand.points[back], hand.points[joint], hand.points[forward])
        val ax = hand.points[back].first - hand.points[joint].first
        val ay = hand.points[back].second - hand.points[joint].second
        val az = hand.depths[back] - hand.depths[joint]
        val bx = hand.points[forward].first - hand.points[joint].first
        val by = hand.points[forward].second - hand.points[joint].second
        val bz = hand.depths[forward] - hand.depths[joint]
        val aLength = kotlin.math.sqrt(ax * ax + ay * ay + az * az)
        val bLength = kotlin.math.sqrt(bx * bx + by * by + bz * bz)
        if (aLength * bLength < 1e-3f) return 1f
        return (ax * bx + ay * by + az * bz) / (aLength * bLength)
    }

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

    const val RAY_HYSTERESIS = 1.35f
}
