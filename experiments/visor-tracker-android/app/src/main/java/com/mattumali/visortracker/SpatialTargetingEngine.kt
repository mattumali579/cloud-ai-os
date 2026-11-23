package com.mattumali.visortracker

import com.mattumali.visortracker.CameraSpatial3D.CameraIntrinsics
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
    val hoverFramesRequired: Int = 4,
    val switchFramesRequired: Int = 5,
    val lostFramesGraceLimit: Int = 3,
    val directionFramesRequired: Int = 3,
    val minHandScore: Float = IndexPoint.MIN_SCORE,
    val maxTargetFrameFraction: Float = IndexPoint.MAX_POINTABLE_FRAME_FRACTION,
    val strict3DMode: Boolean = false,
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
        val sourceLandmark: Int = IndexPoint.INDEX_TIP,
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
        val reason: String = "",
        val ray3D: CameraSpatial3D.SpatialRay3D? = null,
        val hit3D: CameraSpatial3D.HitResult3D? = null,
        val lockProvenance: String = "no_target",
        val lockedObjectId: Int? = lockId,
        val intersectedObjectId: Int? = hitId,
    )

    private var currentState: State = State.NO_HAND
    private var lockedId: Int? = null
    private var pendingHoverId: Int? = null
    private var hoverCount: Int = 0
    private var pendingSwitchId: Int? = null
    private var switchCount: Int = 0
    private var lostFrames: Int = 0
    private var smoothedDirX: Float? = null
    private var smoothedDirY: Float? = null
    private var lastRawDirX: Float? = null
    private var lastRawDirY: Float? = null
    private var stableDirectionFrames: Int = 0
    private var targetMissFrames: Int = 0
    private var activeHandTrackId: Int? = null
    private var activeHandLostFrames: Int = 0

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
        smoothedDirX = null
        smoothedDirY = null
        lastRawDirX = null
        lastRawDirY = null
        stableDirectionFrames = 0
        targetMissFrames = 0
        activeHandTrackId = null
        activeHandLostFrames = 0
    }

    private fun clearTargetState() {
        lockedId = null
        pendingHoverId = null
        hoverCount = 0
        pendingSwitchId = null
        switchCount = 0
        lostFrames = 0
        targetMissFrames = 0
    }

    private fun selectTrackedHand(boxes: List<BoxObservation>, minScore: Float): Pair<BoxObservation?, Boolean> {
        val validHands = boxes.filter { it.kind == "hand" && it.score >= minScore && !it.held && it.points.size >= 21 }
        if (validHands.isEmpty()) {
            activeHandLostFrames++
            if (activeHandLostFrames > lostFramesGraceLimit) {
                activeHandTrackId = null
            }
            return null to false
        }
        val currentId = activeHandTrackId
        if (currentId != null) {
            val matching = validHands.firstOrNull { it.trackId == currentId }
            if (matching != null) {
                activeHandLostFrames = 0
                return matching to false
            }
        }
        val best = validHands.maxByOrNull { it.score }
        val handSwapped = currentId != null && best != null && best.trackId != currentId
        if (best != null && best.trackId > 0) {
            activeHandTrackId = best.trackId
            activeHandLostFrames = 0
        }
        return best to handSwapped
    }

    fun step(frame: FrameDetections): Output {
        val width = frame.width
        val height = frame.height
        if (width <= 0 || height <= 0) {
            return Output(State.NO_HAND, HandPose.NONE, null, null, null, null, lockChanged = false, isReleased = false)
        }

        // 1. Maintain stable hand identity across frames; ownership swap resets hover/lock
        val hadLock = lockedId != null
        val (primaryHand, handSwapped) = selectTrackedHand(frame.boxes, minHandScore)
        if (handSwapped) {
            clearTargetState()
            currentState = State.HAND_TRACKED
            if (hadLock) {
                return Output(
                    state = State.RELEASED,
                    handPose = HandPose.HAND_VISIBLE,
                    lockId = null,
                    hoverId = null,
                    hitId = null,
                    ray = null,
                    lockChanged = true,
                    isReleased = true,
                    reason = "lock released: hand ownership changed",
                )
            }
        }
        if (primaryHand == null) {
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

        // 2. Intentional pointing: construct strictly forward ray from authoritative index digit 8
        val rawRay = constructBestRay(primaryHand, width, height)
        val directionStable = rawRay?.let { updateDirectionStability(it) } ?: false
        val ray = rawRay?.let { stabilizeRay(it) }
        if (ray == null) {
            return handleHandVisible(HandPose.HAND_VISIBLE)
        }

        // 3. Find ray target among valid objects (no fallback, no huge boxes, no fingertip-inside-box hit)
        val validObjects = suppressGenericContainers(
            frame.boxes.filter { isValidTarget(it, width, height) }, width, height
        ).map { candidate ->
            if (candidate.grounded && candidate.trackId == lockedId) {
                expandGroundedLock(candidate, width, height)
            } else candidate
        }
        val target = if (directionStable) {
            findRayIntersection(ray, validObjects, primaryHand, width, height)
        } else null
        val targetId = target?.trackId

        // 3D Spatial Computing: Calculate camera-space ray and 3D surface intersections
        val intrinsics = CameraIntrinsics.forViewport(width, height)
        val armObj = frame.boxes.firstOrNull { it.label.lowercase() in listOf("person", "arm") && it.depths.isNotEmpty() }
        val handBaseDepth = armObj?.depths?.firstOrNull() ?: 0.65f
        val ray3D = CameraSpatial3D.constructHandRay3D(primaryHand, intrinsics, handBaseDepth)

        // Depth frame synchronization verification:
        val depthFrame = frame.depthFrame
        val isDepthSynchronized = depthFrame != null && depthFrame.sequence == frame.sequence

        val depthLookup: ((Float, Float) -> Float)? = if (isDepthSynchronized) {
            { normX, normY -> depthFrame.getDepth(normX, normY) }
        } else null

        // In strict 3D mode, depth frame mismatch or missing fails closed (rejects 3D target acquisition)
        val objectSurfaces3D = if (strict3DMode && !isDepthSynchronized) {
            emptyList()
        } else {
            validObjects.mapNotNull { CameraSpatial3D.reconstructObjectSurface(it, intrinsics, depthLookup) }
        }

        val hit3D = if (ray3D != null && objectSurfaces3D.isNotEmpty()) {
            CameraSpatial3D.intersect3D(
                ray = ray3D,
                surfaces = objectSurfaces3D,
                strictExactOnly = strict3DMode,
                intrinsics = intrinsics,
            )
        } else null

        // In strict 3D mode, ray direction is NEVER bent or modified!
        val finalRay3D = ray3D

        // In strict 3D mode: only TRUE_SURFACE_INTERSECTION with synchronized depth can establish a target lock
        val finalTargetId = if (strict3DMode) {
            if (!isDepthSynchronized) {
                null
            } else if (directionStable && hit3D != null && hit3D.intersectionMode == CameraSpatial3D.IntersectionMode.TRUE_SURFACE_INTERSECTION) {
                hit3D.trackId
            } else null
        } else {
            targetId
        }

        // 4. Temporal state machine update
        return updateStateMachine(
            pose = pose,
            ray = ray,
            targetId = finalTargetId,
            ray3D = finalRay3D,
            hit3D = hit3D,
            validObjects = validObjects,
            depthSynchronized = isDepthSynchronized,
        )
    }

    private fun updateDirectionStability(ray: SpatialRay): Boolean {
        val previousX = lastRawDirX
        val previousY = lastRawDirY
        stableDirectionFrames = if (previousX == null || previousY == null) {
            1
        } else {
            val dot = previousX * ray.dirX + previousY * ray.dirY
            if (dot >= 0.92f) {
                stableDirectionFrames + 1
            } else {
                // A real re-aim must not spend many frames blended with the old
                // direction. Targeting remains disabled until the new raw ray is
                // stable, but visual smoothing restarts from this new episode.
                smoothedDirX = ray.dirX
                smoothedDirY = ray.dirY
                1
            }
        }
        lastRawDirX = ray.dirX
        lastRawDirY = ray.dirY
        return stableDirectionFrames >= directionFramesRequired
    }

    private fun constructBestRay(hand: BoxObservation, width: Int, height: Int): SpatialRay? {
        // Landmark 8 (index tip) with MCP 5 / PIP 6 / DIP 7 knuckle baseline is authoritative.
        // Thumb landmarks 1..4 or other digits must never silently substitute when the index chain fails.
        // Ambiguous digit identity returns null (NO_TARGET).
        return constructForwardRay(hand, width, height)
    }

    private fun stabilizeRay(ray: SpatialRay): SpatialRay {
        val previousX = smoothedDirX
        val previousY = smoothedDirY
        if (previousX == null || previousY == null) {
            smoothedDirX = ray.dirX
            smoothedDirY = ray.dirY
            return ray
        }
        // A side-on/partially occluded finger can make one MediaPipe frame jump
        // toward the palm. Preserve motion, but prevent that single frame from
        // redirecting the ray to a different object.
        val dot = (previousX * ray.dirX + previousY * ray.dirY).coerceIn(-1f, 1f)
        val alpha = if (dot < 0.45f) 0.18f else 0.38f
        var x = previousX * (1f - alpha) + ray.dirX * alpha
        var y = previousY * (1f - alpha) + ray.dirY * alpha
        val length = hypot(x, y)
        if (length > 1e-5f) {
            x /= length
            y /= length
        }
        smoothedDirX = x
        smoothedDirY = y
        return ray.copy(dirX = x, dirY = y)
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
        smoothedDirX = null
        smoothedDirY = null
        lastRawDirX = null
        lastRawDirY = null
        stableDirectionFrames = 0
        currentState = State.HAND_TRACKED
        return Output(State.HAND_TRACKED, pose, null, null, null, null, lockChanged = false, isReleased = false)
    }

    private fun updateStateMachine(
        pose: HandPose,
        ray: SpatialRay,
        targetId: Int?,
        ray3D: CameraSpatial3D.SpatialRay3D? = null,
        hit3D: CameraSpatial3D.HitResult3D? = null,
        validObjects: List<BoxObservation> = emptyList(),
        depthSynchronized: Boolean = true,
    ): Output {
        lostFrames = 0
        var lockChanged = false
        var isReleased = false

        val hitModeProvenance = when {
            strict3DMode && !depthSynchronized -> "depth_frame_mismatch"
            hit3D != null -> {
                if (hit3D.intersectionMode == CameraSpatial3D.IntersectionMode.TRUE_SURFACE_INTERSECTION) "new_3d_hit" else "estimated_proximity"
            }
            else -> "no_target"
        }

        if (lockedId == null) {
            // Not yet locked
            if (targetId == null) {
                targetMissFrames = 0
                pendingHoverId = null
                hoverCount = 0
                currentState = State.POINTING
                return Output(
                    State.POINTING, pose, null, null, null, ray, lockChanged = false, isReleased = false,
                    ray3D = ray3D, hit3D = hit3D, lockProvenance = hitModeProvenance, lockedObjectId = null, intersectedObjectId = null
                )
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
                return Output(
                    State.LOCKED, pose, lockedId, null, targetId, ray, lockChanged = true, isReleased = false,
                    ray3D = ray3D, hit3D = hit3D, lockProvenance = hitModeProvenance, lockedObjectId = lockedId, intersectedObjectId = targetId
                )
            } else {
                currentState = State.HOVER
                return Output(
                    State.HOVER, pose, null, targetId, targetId, ray, lockChanged = false, isReleased = false,
                    ray3D = ray3D, hit3D = hit3D, lockProvenance = hitModeProvenance, lockedObjectId = null, intersectedObjectId = targetId
                )
            }
        } else {
            // Currently locked on lockedId
            val currentLock = lockedId
            val currentObj = validObjects.firstOrNull { it.trackId == currentLock }
            val newObj = validObjects.firstOrNull { it.trackId == targetId }
            val isSameTarget = currentObj != null && newObj != null && currentObj.label == newObj.label

            if (targetId == currentLock || isSameTarget) {
                // Still aiming at locked target (or same physical object whose track ID was updated by upstream tracker)
                if (isSameTarget && targetId != currentLock) {
                    lockedId = targetId
                }
                targetMissFrames = 0
                pendingSwitchId = null
                switchCount = 0
                currentState = State.LOCKED
                return Output(
                    State.LOCKED, pose, lockedId, null, targetId, ray, lockChanged = false, isReleased = false,
                    ray3D = ray3D, hit3D = hit3D, lockProvenance = hitModeProvenance, lockedObjectId = lockedId, intersectedObjectId = targetId
                )
            } else if (targetId != null) {
                targetMissFrames = 0
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
                    return Output(
                        State.LOCKED, pose, targetId, null, targetId, ray, lockChanged = true, isReleased = false,
                        ray3D = ray3D, hit3D = hit3D, lockProvenance = hitModeProvenance, lockedObjectId = targetId, intersectedObjectId = targetId
                    )
                } else {
                    // Switch pending, maintain current lock
                    currentState = State.LOCKED
                    return Output(
                        State.LOCKED, pose, currentLock, null, targetId, ray, lockChanged = false, isReleased = false,
                        ray3D = ray3D, hit3D = hit3D, lockProvenance = "retained_lock_grace", lockedObjectId = currentLock, intersectedObjectId = targetId
                    )
                }
            } else {
                // Ray hit nothing -> hold only briefly, then release. Pointing
                // into empty space must not preserve an unrelated stale lock.
                pendingSwitchId = null
                switchCount = 0
                targetMissFrames++
                if (targetMissFrames > lostFramesGraceLimit) {
                    val released = currentLock != null
                    clearTargetState()
                    currentState = State.RELEASED
                    return Output(
                        State.RELEASED, pose, null, null, null, ray,
                        lockChanged = released, isReleased = released,
                        ray3D = ray3D, hit3D = null,
                        lockProvenance = "no_target", lockedObjectId = null, intersectedObjectId = null
                    )
                }
                currentState = State.LOCKED
                return Output(
                    State.LOCKED, pose, currentLock, null, null, ray, lockChanged = false, isReleased = false,
                    ray3D = ray3D, hit3D = null,
                    lockProvenance = "retained_lock_grace", lockedObjectId = currentLock, intersectedObjectId = null
                )
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

            val dWristIndexTip = jointDist(hand, IndexPoint.WRIST, IndexPoint.INDEX_TIP)
            val dWristIndexMcp = jointDist(hand, IndexPoint.WRIST, IndexPoint.INDEX_MCP)
            val dWristMiddleTip = jointDist(hand, IndexPoint.WRIST, 12)
            val dWristRingTip = jointDist(hand, IndexPoint.WRIST, 16)
            val dWristPinkyTip = jointDist(hand, IndexPoint.WRIST, 20)

            // Index finger extension and alignment
            val indexLength = jointDist(hand, 5, 6) + jointDist(hand, 6, 7) + jointDist(hand, 7, 8)
            val indexNetSpan = jointDist(hand, 5, 8)
            val indexStraightness = if (indexLength > 0f) indexNetSpan / indexLength else 0f
            // World/relative-Z geometry is essential when the finger points into
            // the scene and its 2D projection is foreshortened.
            val indexExtended = dWristIndexTip > dWristIndexMcp * 1.12f && indexStraightness > 0.70f

            // Middle finger straightness for curl check
            val midLength = jointDist(hand, 9, 10) + jointDist(hand, 10, 11) + jointDist(hand, 11, 12)
            val midNetSpan = jointDist(hand, 9, 12)
            val midStraightness = if (midLength > 0f) midNetSpan / midLength else 0f

            // Non-index finger extensions: require both 3D reach and 2D planar extension
            // to prevent occluded curled fingers with noisy MediaPipe Z from falsely triggering OPEN_PALM
            val ring2d = dist(wrist, ringTip)
            val ringMcp2d = dist(wrist, pts[13])
            val pinky2d = dist(wrist, pinkyTip)
            val pinkyMcp2d = dist(wrist, pts[17])

            val middleExtended = dWristMiddleTip > jointDist(hand, 0, 9) * 1.28f && midStraightness > 0.82f
            val ringExtended = dWristRingTip > jointDist(hand, 0, 13) * 1.28f && ring2d > ringMcp2d * 1.25f
            val pinkyExtended = dWristPinkyTip > jointDist(hand, 0, 17) * 1.28f && pinky2d > pinkyMcp2d * 1.25f

            val extendedNonIndexCount = (if (middleExtended) 1 else 0) +
                (if (ringExtended) 1 else 0) +
                (if (pinkyExtended) 1 else 0)

            // A side-on index can be shortened in x/y while remaining straight
            // in MediaPipe's relative-Z geometry. The thumb is used only as a
            // corroborating orientation cue here; the pointing ray still comes
            // from index landmarks 5..8. This accepts the observed 9.5s/15.5s
            // poses without turning an ordinary fist or thumbs-up into a point.
            val thumbLength = jointDist(hand, 1, 2) + jointDist(hand, 2, 3) + jointDist(hand, 3, 4)
            val thumbStraightness = if (thumbLength > 0f) jointDist(hand, 1, 4) / thumbLength else 0f
            val indexDepthSpan = if (hand.depths.size >= 21) {
                kotlin.math.abs(hand.depths[IndexPoint.INDEX_TIP] - hand.depths[IndexPoint.INDEX_MCP])
            } else 0f

            // Occluded curled fingers often receive exaggerated world-Z. Keep
            // open-palm rejection, but cross-check the visible projection: an
            // index that is plainly longer than the other projected digits is
            // not an open palm merely because their uncertain Z values grew.
            val index2d = dist(wrist, indexTip)
            val middle2d = dist(wrist, middleTip)
            val indexMcp2d = dist(wrist, indexMcp)
            val indexVisuallyDominant = index2d > middle2d * 1.24f &&
                index2d > ring2d * 1.34f && index2d > indexMcp2d * 1.18f
            val foreshortenedIndex = indexStraightness >= 0.43f &&
                indexDepthSpan >= 24f &&
                thumbStraightness >= 0.94f

            // Open palm rejection: multiple extended fingers
            if (extendedNonIndexCount >= 2 && !indexVisuallyDominant) {
                return HandPose.OPEN_PALM
            }

            // Closed fist: index is curled
            if (!indexExtended && !foreshortenedIndex && dWristIndexTip < dWristIndexMcp * 1.15f) {
                return HandPose.CLOSED_FIST
            }

            // Phone grip rejection:
            // Hand holding a phone typically shows curled fingers wrapped around the bezel,
            // with wrist resting near screen edge or bottom, or index curved downwards/sideways along edge.
            if (isPhoneGrip(pts, hand, width, height)) {
                return HandPose.PHONE_GRIP
            }

            if (foreshortenedIndex) {
                return HandPose.POINTING_INTENDED
            }

            // Intentional pointing:
            // 1. Index is straight and extends significantly further than middle and ring
            // 2. Index is extended beyond MCP
            // 3. Middle finger is curled (midStraightness < 0.85 or index tip is > middle tip * 1.18)
            val indexDominant = dWristIndexTip > dWristMiddleTip * 1.04f && dWristIndexTip > dWristRingTip * 1.06f
            if (indexExtended && (indexDominant || indexVisuallyDominant) &&
                (midStraightness < 0.88f || dWristIndexTip > dWristMiddleTip * 1.10f || indexVisuallyDominant)
            ) {
                return HandPose.POINTING_INTENDED
            }

            return HandPose.HAND_VISIBLE
        }

        /** Compact geometry evidence used by replay/device diagnostics for rejected hands. */
        fun poseEvidenceSummary(hand: BoxObservation): String {
            if (hand.points.size < 21) return "landmarks=${hand.points.size}"
            val indexLength = jointDist(hand, 5, 6) + jointDist(hand, 6, 7) + jointDist(hand, 7, 8)
            val indexStraightness = if (indexLength > 0f) jointDist(hand, 5, 8) / indexLength else 0f
            val wristBase = jointDist(hand, 0, 5)
            val wristRatio = if (wristBase > 0f) jointDist(hand, 0, 8) / wristBase else 0f
            val depthSpan = if (hand.depths.size >= 21) {
                kotlin.math.abs(hand.depths[8] - hand.depths[5])
            } else 0f
            val thumbLength = jointDist(hand, 1, 2) + jointDist(hand, 2, 3) + jointDist(hand, 3, 4)
            val thumbStraightness = if (thumbLength > 0f) jointDist(hand, 1, 4) / thumbLength else 0f
            return "indexStraight=%.2f wristRatio=%.2f depthSpan=%.1f thumbStraight=%.2f".format(
                indexStraightness, wristRatio, depthSpan, thumbStraightness,
            )
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
            // The low-score COCO tail produced the confirmed 0.8s suitcase
            // false lock. Grounded VLM boxes carry no calibrated confidence;
            // local detector boxes must clear a reliability floor.
            if (!box.grounded && box.score < MIN_LOCAL_TARGET_SCORE) return false

            val area = (box.right - box.left) * (box.bottom - box.top)
            val frameArea = width.toFloat() * height.toFloat()
            // A fallback localization must be specific. Large grounded regions
            // are usually structural/background guesses and can swallow several
            // unrelated pointable objects.
            if (box.grounded && area > frameArea * MAX_GROUNDED_FRAME_FRACTION) return false
            // Ignore boxes covering more than 35% of the frame (wearer arm, whole background, TV screen taking whole room)
            if (area > frameArea * 0.35f) return false

            // Reject person labels that represent wearer arm
            val label = box.label.lowercase()
            if (label == "person" || label == "arm") return false

            return true
        }

        const val MIN_LOCAL_TARGET_SCORE = 0.20f
        const val MAX_GROUNDED_FRAME_FRACTION = 0.14f

        /** Reject broad category-free masks that swallow real, specific detections. */
        fun suppressGenericContainers(
            objects: List<BoxObservation>, width: Int, height: Int
        ): List<BoxObservation> {
            val frameArea = width.toFloat() * height.toFloat()
            val specific = objects.filter { it.label.lowercase() != LabelPolicy.UNCERTAIN }
            return objects.filter { candidate ->
                if (candidate.label.lowercase() != LabelPolicy.UNCERTAIN) return@filter true
                val area = candidate.area()
                if (area > frameArea * 0.20f) return@filter false
                val swallowed = specific.count { other ->
                    val overlap = overlapArea(candidate, other)
                    overlap > other.area() * 0.55f && area > other.area() * 1.6f
                }
                swallowed == 0
            }
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
            // For coarse bounding boxes without mask points, exclude if fingertip is inside box.
            // For segmented objects with polygon masks, allow ray-mask intersection.
            val candidateObjects = objects.filter { box ->
                if (box.grounded) {
                    val inside = tipX in box.left..box.right && tipY in box.top..box.bottom
                    if (!inside) return@filter true
                    // Grounded boxes may legitimately contain the fingertip when
                    // the camera-view finger occludes the intended object. Require
                    // the tip to be clearly inside, not grazing a noisy VLM edge.
                    val inset = min(8f, min(box.right - box.left, box.bottom - box.top) * 0.15f)
                    return@filter tipX > box.left + inset && tipX < box.right - inset &&
                        tipY > box.top + inset && tipY < box.bottom - inset
                }
                if (box.points.isNotEmpty()) {
                    true
                } else {
                    val inside = tipX in box.left..box.right && tipY in box.top..box.bottom
                    !inside
                }
            }

            if (candidateObjects.isEmpty()) return null

            val hits = mutableListOf<Pair<BoxObservation, Float>>()
            for (box in candidateObjects) {
                // If box has instance segmentation mask points, verify ray passes near or through mask
                val t = if (box.points.isNotEmpty() && box.kind == "object") {
                    rayMaskIntersectionDistance(ray, box)
                } else {
                    val intersectionBox = if (box.grounded) {
                        // Grounded localization is a coarse normalized box and
                        // side-on landmark projection has measurable angular
                        // error. Use a bounded acquisition corridor; the VLM box
                        // itself remains unchanged and still needs confirmation.
                        val margin = min(width, height) * 0.06f
                        box.copy(
                            left = (box.left - margin).coerceAtLeast(0f),
                            top = (box.top - margin).coerceAtLeast(0f),
                            right = (box.right + margin).coerceAtMost(width.toFloat()),
                            bottom = (box.bottom + margin).coerceAtMost(height.toFloat()),
                        )
                    } else box
                    rayBoxIntersectionDistance(ray, intersectionBox)
                }

                if (t != null && t in 0f..ray.reach) {
                    hits.add(box to t)
                }
            }

            if (hits.isEmpty()) return null
            if (hits.size == 1) return hits[0].first

            // When multiple candidates intersect the ray, incorporate relative depth
            // to resolve foreground vs background occlusion without claiming uncalibrated metric scale.
            // Candidates with valid depth evidence are compared: if one is significantly closer (lower relative depth),
            // it occludes the background candidate.
            hits.sortWith { a, b ->
                val boxA = a.first
                val boxB = b.first
                val tA = a.second
                val tB = b.second

                // Check if the two candidates are in occlusion conflict:
                // They either overlap spatially in 2D projection or are close along the ray trajectory (< 150 px)
                val overlap = overlapArea(boxA, boxB) > 0f || kotlin.math.abs(tA - tB) < 150f
                val depthA = boxA.depths.firstOrNull()
                val depthB = boxB.depths.firstOrNull()

                if (overlap && depthA != null && depthB != null && kotlin.math.abs(depthA - depthB) > 0.35f) {
                    depthA.compareTo(depthB)
                } else {
                    tA.compareTo(tB)
                }
            }

            return hits.first().first
        }

        private fun expandGroundedLock(box: BoxObservation, width: Int, height: Int): BoxObservation {
            val margin = min(width, height) * 0.025f
            return box.copy(
                left = (box.left - margin).coerceAtLeast(0f),
                top = (box.top - margin).coerceAtLeast(0f),
                right = (box.right + margin).coerceAtMost(width.toFloat()),
                bottom = (box.bottom + margin).coerceAtMost(height.toFloat()),
            )
        }

        /**
         * Computes ray intersection distance with instance segmentation mask points.
         */
        fun rayMaskIntersectionDistance(ray: SpatialRay, box: BoxObservation): Float? {
            val pts = box.points
            if (pts.isEmpty()) return null

            // 1. Ray-Segment Edge Crossings: If ray crosses any polygon contour edge, it intersects the mask!
            var minT = Float.MAX_VALUE
            var crossed = false
            val n = pts.size
            for (i in 0 until n) {
                val a = pts[i]
                val b = pts[(i + 1) % n]
                val dx = b.first - a.first
                val dy = b.second - a.second
                val denom = ray.dirX * dy - ray.dirY * dx
                if (kotlin.math.abs(denom) > 1e-6f) {
                    val qx = a.first - ray.tipX
                    val qy = a.second - ray.tipY
                    val t = (qx * dy - qy * dx) / denom
                    val u = (qx * ray.dirY - qy * ray.dirX) / denom
                    if (t in 0f..ray.reach && u in 0f..1f) {
                        crossed = true
                        if (t < minT) minT = t
                    }
                }
            }
            if (crossed) return minT

            // 2. Proximity to contour vertices within threshold (for near-miss tolerance)
            val thresholdPx = 45f
            var minProj = Float.MAX_VALUE
            var nearHit = false

            for (p in pts) {
                val vx = p.first - ray.tipX
                val vy = p.second - ray.tipY
                val proj = vx * ray.dirX + vy * ray.dirY
                if (proj in 0f..ray.reach) {
                    val perpDist = kotlin.math.abs(vx * ray.dirY - vy * ray.dirX)
                    if (perpDist <= thresholdPx) {
                        nearHit = true
                        if (proj < minProj) minProj = proj
                    }
                }
            }
            return if (nearHit) minProj else null
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

        private fun jointDist(hand: BoxObservation, a: Int, b: Int): Float {
            val pa = hand.points[a]
            val pb = hand.points[b]
            val dx = pa.first - pb.first
            val dy = pa.second - pb.second
            val dz = if (hand.depths.size >= 21 && hand.depths[a].isFinite() && hand.depths[b].isFinite()) {
                hand.depths[a] - hand.depths[b]
            } else 0f
            return kotlin.math.sqrt(dx * dx + dy * dy + dz * dz)
        }

        private fun overlapArea(a: BoxObservation, b: BoxObservation): Float {
            val w = max(0f, min(a.right, b.right) - max(a.left, b.left))
            val h = max(0f, min(a.bottom, b.bottom) - max(a.top, b.top))
            return w * h
        }
    }
}
