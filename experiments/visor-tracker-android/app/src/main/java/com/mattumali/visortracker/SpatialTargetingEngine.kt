package com.mattumali.visortracker

import kotlin.math.acos
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * Canonical end-to-end spatial targeting engine for Visor (GitHub Issue #40).
 *
 * Implements:
 * 1. Stable hand pose classification: isolates intentional pointing from open palm,
 *    phone grip, clenched fist, and relaxed hand.
 * 2. Strict forward camera-space ray casting: NO backwards ray flips, NO fingertip-inside-box
 *    automatic hits, and NO nearest/center/highest-score fallback.
 * 3. Temporal state machine: NO_HAND -> HAND_TRACKED -> POINTING -> HOVER -> LOCKED -> RELEASED.
 *    - Stable hover (>= hoverFrames) automatically acquires target.
 *    - Target switching requires sustained aim (>= switchFrames) with hysteresis.
 *    - Pointing disappearance / release releases the lock after bounded lost-frame grace.
 */
class SpatialTargetingEngine(
    val hoverFramesRequired: Int = 2,
    val switchFramesRequired: Int = 5,
    val lostFramesGraceLimit: Int = 3,
    val minHandScore: Float = IndexPoint.MIN_SCORE,
    val maxTargetFrameFraction: Float = IndexPoint.MAX_POINTABLE_FRAME_FRACTION,
) {
    enum class State {
        NO_HAND,
        HAND_TRACKED,
        POINTING,
        HOVER,
        LOCKED,
        RELEASED,
    }

    enum class HandPose {
        NONE,
        HAND_VISIBLE,
        OPEN_PALM,
        PHONE_GRIP,
        CLOSED_FIST,
        POINTING_INTENDED,
    }

    data class SpatialRay(
        val tipX: Float,
        val tipY: Float,
        val dirX: Float,
        val dirY: Float,
        val dirZ: Float,
        val reach: Float,
        val joints: List<Pair<Float, Float>>,
    )

    data class Output(
        val state: State,
        val handPose: HandPose,
        val lockId: Int?,
        val hoverId: Int?,
        val hitId: Int?,
        val ray: SpatialRay?,
        val lockChanged: Boolean,
        val isReleased: Boolean,
    )

    private var currentState: State = State.NO_HAND
    private var lockedId: Int? = null
    private var pendingHoverId: Int? = null
    private var hoverCount: Int = 0
    private var pendingSwitchId: Int? = null
    private var switchCount: Int = 0
    private var lostFrames: Int = 0

    val currentLockedId: Int? get() = lockedId
    val currentHoverId: Int? get() = pendingHoverId

    fun reset() {
        currentState = State.NO_HAND
        lockedId = null
        pendingHoverId = null
        hoverCount = 0
        pendingSwitchId = null
        switchCount = 0
        lostFrames = 0
    }

    fun step(frame: FrameDetections): Output {
        val width = frame.width
        val height = frame.height
        if (width <= 0 || height <= 0) {
            return Output(State.NO_HAND, HandPose.NONE, null, null, null, null, lockChanged = false, isReleased = false)
        }

        // 1. Check for arm / no hand
        val hasArm = frame.boxes.any { it.kind == "arm" || HandPriority.wearerArm(it, width, height) }
        val primaryHand = selectPrimaryHand(frame.boxes, minHandScore)
        if (primaryHand == null) {
            if (hasArm) {
                return handleNonPointingHand(HandPose.HAND_VISIBLE)
            }
            return handleNoHand()
        }

        val pose = classifyPose(primaryHand, width, height)

        // If open palm, phone grip, or closed fist -> NEVER point or lock
        if (pose == HandPose.OPEN_PALM || pose == HandPose.PHONE_GRIP || pose == HandPose.CLOSED_FIST) {
            return handleNonPointingHand(pose)
        }

        if (pose != HandPose.POINTING_INTENDED) {
            return handleHandVisible(pose)
        }

        // 2. Intentional pointing: construct strictly forward ray
        val ray = constructForwardRay(primaryHand, width, height)
        if (ray == null) {
            return handleHandVisible(HandPose.HAND_VISIBLE)
        }

        // 3. Find ray target among valid objects (no fallback, no huge boxes, no fingertip-inside-box hit)
        val validObjects = frame.boxes.filter { isValidTarget(it, width, height) }
        val target = findRayIntersection(ray, validObjects, primaryHand, width, height)
        val targetId = target?.trackId

        // 4. Temporal state machine update
        return updateStateMachine(pose, ray, targetId)
    }

    private fun handleNoHand(): Output {
        if (lockedId != null) {
            lostFrames++
            if (lostFrames <= lostFramesGraceLimit) {
                // Temporary lost frame grace
                return Output(
                    state = currentState,
                    handPose = HandPose.NONE,
                    lockId = lockedId,
                    hoverId = null,
                    hitId = null,
                    ray = null,
                    lockChanged = false,
                    isReleased = false,
                )
            }
            // Grace expired -> RELEASED
            val hadLock = lockedId != null
            reset()
            return Output(
                state = State.RELEASED,
                handPose = HandPose.NONE,
                lockId = null,
                hoverId = null,
                hitId = null,
                ray = null,
                lockChanged = hadLock,
                isReleased = true,
            )
        }
        reset()
        return Output(State.NO_HAND, HandPose.NONE, null, null, null, null, lockChanged = false, isReleased = false)
    }

    private fun handleNonPointingHand(pose: HandPose): Output {
        // Open palm, phone grip, or closed fist immediately breaks hover and releases lock
        val hadLock = lockedId != null
        reset()
        val newState = if (pose == HandPose.CLOSED_FIST) State.RELEASED else State.HAND_TRACKED
        return Output(
            state = newState,
            handPose = pose,
            lockId = null,
            hoverId = null,
            hitId = null,
            ray = null,
            lockChanged = hadLock,
            isReleased = hadLock,
        )
    }

    private fun handleHandVisible(pose: HandPose): Output {
        // Hand is tracked but not pointing
        if (lockedId != null) {
            lostFrames++
            if (lostFrames <= lostFramesGraceLimit) {
                return Output(
                    state = currentState,
                    handPose = pose,
                    lockId = lockedId,
                    hoverId = null,
                    hitId = null,
                    ray = null,
                    lockChanged = false,
                    isReleased = false,
                )
            }
            val hadLock = lockedId != null
            reset()
            return Output(
                state = State.RELEASED,
                handPose = pose,
                lockId = null,
                hoverId = null,
                hitId = null,
                ray = null,
                lockChanged = hadLock,
                isReleased = true,
            )
        }
        pendingHoverId = null
        hoverCount = 0
        pendingSwitchId = null
        switchCount = 0
        currentState = State.HAND_TRACKED
        return Output(State.HAND_TRACKED, pose, null, null, null, null, lockChanged = false, isReleased = false)
    }

    private fun updateStateMachine(pose: HandPose, ray: SpatialRay, targetId: Int?): Output {
        lostFrames = 0
        var lockChanged = false
        var isReleased = false

        if (lockedId == null) {
            // Not yet locked
            if (targetId == null) {
                pendingHoverId = null
                hoverCount = 0
                currentState = State.POINTING
                return Output(State.POINTING, pose, null, null, null, ray, lockChanged = false, isReleased = false)
            }

            // Ray intersects a candidate target
            if (pendingHoverId == targetId) {
                hoverCount++
            } else {
                pendingHoverId = targetId
                hoverCount = 1
            }

            if (hoverCount >= hoverFramesRequired) {
                // HOVER -> LOCKED
                lockedId = targetId
                lockChanged = true
                currentState = State.LOCKED
                pendingHoverId = null
                hoverCount = 0
                return Output(State.LOCKED, pose, lockedId, null, targetId, ray, lockChanged = true, isReleased = false)
            } else {
                currentState = State.HOVER
                return Output(State.HOVER, pose, null, targetId, targetId, ray, lockChanged = false, isReleased = false)
            }
        } else {
            // Currently locked on lockedId
            val currentLock = lockedId
            if (targetId == currentLock) {
                // Still aiming at locked target
                pendingSwitchId = null
                switchCount = 0
                currentState = State.LOCKED
                return Output(State.LOCKED, pose, currentLock, null, currentLock, ray, lockChanged = false, isReleased = false)
            } else if (targetId != null) {
                // Aiming at a DIFFERENT target -> test deliberate switch hysteresis
                if (pendingSwitchId == targetId) {
                    switchCount++
                } else {
                    pendingSwitchId = targetId
                    switchCount = 1
                }

                if (switchCount >= switchFramesRequired) {
                    // SWITCH confirmed!
                    lockedId = targetId
                    pendingSwitchId = null
                    switchCount = 0
                    lockChanged = true
                    currentState = State.LOCKED
                    return Output(State.LOCKED, pose, targetId, null, targetId, ray, lockChanged = true, isReleased = false)
                } else {
                    // Switch pending, maintain current lock
                    currentState = State.LOCKED
                    return Output(State.LOCKED, pose, currentLock, null, targetId, ray, lockChanged = false, isReleased = false)
                }
            } else {
                // Ray hit nothing -> hold lock briefly (hysteresis against temporary dropout)
                pendingSwitchId = null
                switchCount = 0
                currentState = State.LOCKED
                return Output(State.LOCKED, pose, currentLock, null, null, ray, lockChanged = false, isReleased = false)
            }
        }
    }

    companion object {
        fun selectPrimaryHand(boxes: List<BoxObservation>, minScore: Float): BoxObservation? {
            return boxes.filter { it.kind == "hand" && it.score >= minScore && !it.held && it.points.size >= 21 }
                .maxByOrNull { it.score }
        }

        /**
         * Robust hand pose classification.
         * Distinguishes intentional pointing from open palm, phone grip, fist, and neutral.
         */
        fun classifyPose(hand: BoxObservation, width: Int, height: Int): HandPose {
            val pts = hand.points
            if (pts.size < 21) return HandPose.NONE

            val wrist = pts[IndexPoint.WRIST]
            val indexTip = pts[IndexPoint.INDEX_TIP]
            val indexPip = pts[IndexPoint.INDEX_PIP]
            val indexMcp = pts[IndexPoint.INDEX_MCP]

            val middleTip = pts[12]
            val middlePip = pts[10]
            val middleMcp = pts[9]

            val ringTip = pts[16]
            val ringPip = pts[14]

            val pinkyTip = pts[20]
            val pinkyPip = pts[18]

            val dWristIndexTip = dist(wrist, indexTip)
            val dWristIndexMcp = dist(wrist, indexMcp)
            val dWristMiddleTip = dist(wrist, middleTip)
            val dWristRingTip = dist(wrist, ringTip)
            val dWristPinkyTip = dist(wrist, pinkyTip)

            // Index finger extension and alignment
            val indexLength = dist(indexMcp, indexPip) + dist(indexPip, pts[IndexPoint.INDEX_DIP]) + dist(pts[IndexPoint.INDEX_DIP], indexTip)
            val indexNetSpan = dist(indexMcp, indexTip)
            val indexStraightness = if (indexLength > 0f) indexNetSpan / indexLength else 0f
            val indexExtended = dWristIndexTip > dWristIndexMcp * 1.25f && indexStraightness > 0.75f

            // Middle finger straightness for curl check
            val midLength = dist(middleMcp, middlePip) + dist(middlePip, pts[11]) + dist(pts[11], middleTip)
            val midNetSpan = dist(middleMcp, middleTip)
            val midStraightness = if (midLength > 0f) midNetSpan / midLength else 0f

            // Non-index finger extensions
            val middleExtended = dWristMiddleTip > dist(wrist, middleMcp) * 1.35f && midStraightness > 0.85f
            val ringExtended = dWristRingTip > dist(wrist, pts[13]) * 1.35f
            val pinkyExtended = dWristPinkyTip > dist(wrist, pts[17]) * 1.35f

            val extendedNonIndexCount = (if (middleExtended) 1 else 0) +
                (if (ringExtended) 1 else 0) +
                (if (pinkyExtended) 1 else 0)

            // Open palm rejection: multiple extended fingers
            if (extendedNonIndexCount >= 2) {
                return HandPose.OPEN_PALM
            }

            // Closed fist: index is curled
            if (!indexExtended && dWristIndexTip < dWristIndexMcp * 1.15f) {
                return HandPose.CLOSED_FIST
            }

            // Phone grip rejection:
            // Hand holding a phone typically shows curled fingers wrapped around the bezel,
            // with wrist resting near screen edge or bottom, or index curved downwards/sideways along edge.
            if (isPhoneGrip(pts, hand, width, height)) {
                return HandPose.PHONE_GRIP
            }

            // Intentional pointing:
            // 1. Index is straight and extends significantly further than middle and ring
            // 2. Index is extended beyond MCP
            // 3. Middle finger is curled (midStraightness < 0.85 or index tip is > middle tip * 1.18)
            val indexDominant = dWristIndexTip > dWristMiddleTip * 1.10f && dWristIndexTip > dWristRingTip * 1.12f
            if (indexExtended && indexDominant && (midStraightness < 0.85f || dWristIndexTip > dWristMiddleTip * 1.18f)) {
                return HandPose.POINTING_INTENDED
            }

            return HandPose.HAND_VISIBLE
        }

        private fun isPhoneGrip(pts: List<Pair<Float, Float>>, hand: BoxObservation, fw: Int, fh: Int): Boolean {
            val wrist = pts[IndexPoint.WRIST]
            // If wrist is right on bottom margin (typical phone holding hand)
            val margin = max(24f, fh * 0.04f)
            val nearBottomEdge = wrist.second >= fh - margin
            val nearSideEdge = wrist.first <= margin || wrist.first >= fw - margin

            // Finger spread in phone grip: fingers are bent with low straightness across all knuckles
            val indexTip = pts[IndexPoint.INDEX_TIP]
            val indexMcp = pts[IndexPoint.INDEX_MCP]
            val indexPip = pts[IndexPoint.INDEX_PIP]
            val dIndex = dist(indexTip, indexMcp)
            val dIndexCurled = dist(indexTip, wrist) < dist(indexMcp, wrist) * 1.25f

            // Aspect ratio of the hand box when holding a phone
            val boxW = hand.right - hand.left
            val boxH = hand.bottom - hand.top
            val isSmallCornerGrip = (nearBottomEdge || nearSideEdge) && (boxW < fw * 0.25f || boxH < fh * 0.25f)

            if (isSmallCornerGrip && dIndexCurled) {
                return true
            }

            // If index is not extended significantly forward beyond PIP
            val indexForward = (indexTip.second - indexPip.second)
            if (nearBottomEdge && indexForward > 0f) {
                // Pointing down towards oneself / phone
                return true
            }

            return false
        }

        /**
         * Construct strictly forward-directed spatial ray.
         * Never reverses or flips backward!
         */
        fun constructForwardRay(hand: BoxObservation, width: Int, height: Int): SpatialRay? {
            val pts = hand.points
            if (pts.size < 21) return null

            val tip = pts[IndexPoint.INDEX_TIP]
            val dip = pts[IndexPoint.INDEX_DIP]
            val pip = pts[IndexPoint.INDEX_PIP]
            val mcp = pts[IndexPoint.INDEX_MCP]
            val wrist = pts[IndexPoint.WRIST]

            // 2D vector along index finger: from MCP to TIP for directional stability,
            // fallback to PIP to TIP if MCP is coincident with TIP
            var dx = tip.first - mcp.first
            var dy = tip.second - mcp.second
            var len = hypot(dx, dy)

            if (len < 1e-4f) {
                dx = tip.first - pip.first
                dy = tip.second - pip.second
                len = hypot(dx, dy)
            }
            if (len < 1e-4f) return null

            val dirX = dx / len
            val dirY = dy / len

            // 3D Z coordinate from depths if available
            val tipZ = if (hand.depths.size > IndexPoint.INDEX_TIP) hand.depths[IndexPoint.INDEX_TIP] else 0f
            val pipZ = if (hand.depths.size > IndexPoint.INDEX_PIP) hand.depths[IndexPoint.INDEX_PIP] else 0f
            val dirZ = tipZ - pipZ

            // Check direction relative to hand root (wrist -> tip)
            // Ray must point AWAY from wrist (forward into scene), NEVER backwards into the user!
            val fromWristX = tip.first - wrist.first
            val fromWristY = tip.second - wrist.second
            val dotWrist = dirX * fromWristX + dirY * fromWristY
            if (dotWrist < 0f) {
                // Ray would point backward into the hand/arm! Strict reject - NO BACKWARD FLIP!
                return null
            }

            val reach = hypot(width.toFloat(), height.toFloat()) * 1.5f
            val joints = listOf(wrist, mcp, pip, dip, tip)

            return SpatialRay(
                tipX = tip.first,
                tipY = tip.second,
                dirX = dirX,
                dirY = dirY,
                dirZ = dirZ,
                reach = reach,
                joints = joints,
            )
        }

        fun isValidTarget(box: BoxObservation, width: Int, height: Int): Boolean {
            if (box.held || box.kind != "object" || box.trackId <= 0) return false
            if (box.right <= box.left || box.bottom <= box.top) return false

            val area = (box.right - box.left) * (box.bottom - box.top)
            val frameArea = width.toFloat() * height.toFloat()
            // Ignore boxes covering more than 35% of the frame (wearer arm, whole background, TV screen taking whole room)
            if (area > frameArea * 0.35f) return false

            // Reject person labels that represent wearer arm
            val label = box.label.lowercase()
            if (label == "person" || label == "arm") return false

            return true
        }

        /**
         * Ray-box intersection.
         * Implements:
         * - NO fingertip-inside-box=zero automatic hit!
         * - NO nearest/center/highest-score fallback!
         * - Selects the first object along ray in forward trajectory.
         */
        fun findRayIntersection(
            ray: SpatialRay,
            objects: List<BoxObservation>,
            hand: BoxObservation,
            width: Int,
            height: Int,
        ): BoxObservation? {
            if (objects.isEmpty()) return null

            val tipX = ray.tipX
            val tipY = ray.tipY

            // Disallow fingertip-inside-box false hits:
            // Any object whose box already contains the fingertip is excluded unless the finger was aimed from outside.
            val candidateObjects = objects.filter { box ->
                val inside = tipX in box.left..box.right && tipY in box.top..box.bottom
                !inside
            }

            if (candidateObjects.isEmpty()) return null

            var bestBox: BoxObservation? = null
            var minT = Float.MAX_VALUE

            for (box in candidateObjects) {
                // If box has instance segmentation mask points, verify ray passes near or through mask
                val t = if (box.points.isNotEmpty() && box.kind == "object") {
                    rayMaskIntersectionDistance(ray, box)
                } else {
                    rayBoxIntersectionDistance(ray, box)
                }

                if (t != null && t in 0f..ray.reach) {
                    // 3D Depth consistency: if depth evidence is present, verify along ray trajectory
                    if (box.depths.isNotEmpty() && hand.depths.isNotEmpty()) {
                        val handDepth = hand.depths.getOrElse(IndexPoint.INDEX_TIP) { 0.3f }
                        val objectDepth = box.depths[0]
                        // When ray points away into space (dirZ > 0), reject objects located in front of hand
                        if (ray.dirZ > 0.05f && objectDepth < handDepth - 0.25f) {
                            continue
                        }
                    }

                    if (t < minT) {
                        minT = t
                        bestBox = box
                    }
                }
            }

            return bestBox
        }

        /**
         * Computes ray intersection distance with instance segmentation mask points.
         */
        fun rayMaskIntersectionDistance(ray: SpatialRay, box: BoxObservation): Float? {
            val pts = box.points
            if (pts.isEmpty()) return null

            val thresholdPx = 35f
            var minProj = Float.MAX_VALUE
            var hit = false

            for (p in pts) {
                val vx = p.first - ray.tipX
                val vy = p.second - ray.tipY
                val proj = vx * ray.dirX + vy * ray.dirY
                if (proj in 0f..ray.reach) {
                    val perpDist = kotlin.math.abs(vx * ray.dirY - vy * ray.dirX)
                    if (perpDist <= thresholdPx) {
                        hit = true
                        if (proj < minProj) minProj = proj
                    }
                }
            }
            return if (hit) minProj else null
        }

        /**
         * Liang-Barsky / slab method ray-AABB intersection distance.
         * Returns forward parameter t >= 0 where ray(t) enters box, or null if no hit.
         */
        fun rayBoxIntersectionDistance(ray: SpatialRay, box: BoxObservation): Float? {
            var tmin = 0f
            var tmax = ray.reach

            // X slab
            if (kotlin.math.abs(ray.dirX) < 1e-6f) {
                if (ray.tipX < box.left || ray.tipX > box.right) return null
            } else {
                val invDir = 1f / ray.dirX
                var t1 = (box.left - ray.tipX) * invDir
                var t2 = (box.right - ray.tipX) * invDir
                if (t1 > t2) {
                    val tmp = t1
                    t1 = t2
                    t2 = tmp
                }
                tmin = max(tmin, t1)
                tmax = min(tmax, t2)
                if (tmin > tmax) return null
            }

            // Y slab
            if (kotlin.math.abs(ray.dirY) < 1e-6f) {
                if (ray.tipY < box.top || ray.tipY > box.bottom) return null
            } else {
                val invDir = 1f / ray.dirY
                var t1 = (box.top - ray.tipY) * invDir
                var t2 = (box.bottom - ray.tipY) * invDir
                if (t1 > t2) {
                    val tmp = t1
                    t1 = t2
                    t2 = tmp
                }
                tmin = max(tmin, t1)
                tmax = min(tmax, t2)
                if (tmin > tmax) return null
            }

            return if (tmin >= 0f) tmin else null
        }

        private fun dist(a: Pair<Float, Float>, b: Pair<Float, Float>): Float {
            return hypot(a.first - b.first, a.second - b.second)
        }
    }
}
