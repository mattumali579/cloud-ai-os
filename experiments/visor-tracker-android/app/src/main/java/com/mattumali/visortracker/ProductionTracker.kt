package com.mattumali.visortracker

/**
 * The same association, gesture, and lock steps the overlay uses, with no
 * camera. Replay feeds detector boxes in and reads decisions out.
 *
 * Backed by SpatialTargetingEngine for canonical end-to-end computer vision (Issue #40).
 */
class ProductionTracker(
    val engine: SpatialTargetingEngine = SpatialTargetingEngine()
) {
    private val tracks = TrackIds()

    data class Step(
        val state: GestureState,
        val lockId: Int?,
        val hitId: Int?,
        val ray: Boolean,
        val tipX: Float = 0f,
        val tipY: Float = 0f,
        val dirX: Float = 0f,
        val dirY: Float = 0f,
        val reach: Float = 0f,
        val boxes: List<BoxObservation> = emptyList(),
        val targetingState: SpatialTargetingEngine.State = SpatialTargetingEngine.State.NO_HAND,
        val gestureReason: String = "",
        val targetReason: String = "",
        val landmark8X: Float = 0f,
        val landmark8Y: Float = 0f,
        val raySourceLandmark: Int = IndexPoint.INDEX_TIP,
        val ray3D: CameraSpatial3D.SpatialRay3D? = null,
        val hit3D: CameraSpatial3D.HitResult3D? = null,
        val lockProvenance: String = "no_target",
        val lockedObjectId: Int? = lockId,
        val intersectedObjectId: Int? = hitId,
        val frameId: Long = 0L,
        val depthFrameId: Long = 0L,
    )

    fun reset() {
        engine.reset()
    }

    fun step(
        detections: List<BoxObservation>,
        width: Int,
        height: Int,
        capturedAtMs: Long = 0L,
        sequence: Long = 0L,
    ): Step {
        val arm = detections.firstOrNull { HandPriority.wearerArm(it, width, height) }
        val marked = if (arm != null) detections + HandTrack.armMarker(arm) else detections
        val identified = tracks.update(HandPriority.apply(marked, width, height))
            .filter { box -> !box.held || box.kind == "hand" }
        val frame = FrameDetections(width, height, identified, sequence = sequence, capturedAtMs = capturedAtMs)

        val output = engine.step(frame)

        // Map SpatialTargetingEngine state & pose to GestureState for backward compatibility
        val gestureState = when {
            identified.any { it.kind == "arm" } && output.handPose != SpatialTargetingEngine.HandPose.POINTING_INTENDED -> GestureState.ARM_VISIBLE
            output.handPose == SpatialTargetingEngine.HandPose.POINTING_INTENDED -> GestureState.POINTING_CONFIRMED
            output.handPose == SpatialTargetingEngine.HandPose.OPEN_PALM ||
                output.handPose == SpatialTargetingEngine.HandPose.PHONE_GRIP ||
                output.handPose == SpatialTargetingEngine.HandPose.CLOSED_FIST ||
                output.handPose == SpatialTargetingEngine.HandPose.HAND_VISIBLE -> GestureState.HAND_VISIBLE
            else -> GestureState.NONE
        }

        val ray = output.ray
        val freshHand = identified.firstOrNull { it.kind == "hand" && !it.held && it.points.size >= 21 }
        val landmark8 = freshHand?.points?.getOrNull(IndexPoint.INDEX_TIP)
        val gestureReason = when {
            output.handPose == SpatialTargetingEngine.HandPose.NONE -> "rejected: no fresh 21-landmark hand"
            output.handPose == SpatialTargetingEngine.HandPose.OPEN_PALM -> "rejected: open palm"
            output.handPose == SpatialTargetingEngine.HandPose.PHONE_GRIP -> "rejected: phone-grip geometry"
            output.handPose == SpatialTargetingEngine.HandPose.CLOSED_FIST -> "rejected: curled index / closed fist"
            output.handPose == SpatialTargetingEngine.HandPose.HAND_VISIBLE ->
                "rejected: index extension or dominance insufficient; " +
                    (freshHand?.let { SpatialTargetingEngine.poseEvidenceSummary(it) } ?: "no fresh hand")
            ray == null -> "rejected: ambiguous digit or no valid index ray"
            else -> "accepted: intentional point with stabilized forward ray"
        }
        val targetReason = when {
            output.lockId != null && output.hit3D != null && output.hitId == output.lockId ->
                "selected 3D: confirmed 3D ray-surface intersection (${output.hit3D.intersectionMode.name}, s=${String.format(java.util.Locale.US, "%.2f", output.hit3D.distanceAlongRay)}m, unc=${String.format(java.util.Locale.US, "%.3f", output.hit3D.geometricUncertainty)})"
            output.lockId != null && output.hitId == output.lockId -> "selected: confirmed ray-mask intersection"
            output.lockId != null && output.hitId != null -> "held: switch candidate awaiting confirmation"
            output.lockId != null -> "held: bounded target dropout tolerance"
            output.hit3D != null ->
                "hover 3D: valid 3D ray-surface intersection (${output.hit3D.intersectionMode.name}) awaiting confirmation"
            output.hitId != null -> "hover: valid ray-mask intersection awaiting confirmation"
            ray != null -> "NO_TARGET: no valid specific ray-mask intersection"
            else -> "NO_TARGET: pointing gesture not accepted"
        }
        return Step(
            state = gestureState,
            lockId = output.lockId,
            hitId = output.hitId,
            ray = ray != null,
            tipX = ray?.tipX ?: 0f,
            tipY = ray?.tipY ?: 0f,
            dirX = ray?.dirX ?: 0f,
            dirY = ray?.dirY ?: 0f,
            reach = ray?.reach ?: 0f,
            boxes = identified,
            targetingState = output.state,
            gestureReason = gestureReason,
            targetReason = targetReason,
            landmark8X = landmark8?.first ?: 0f,
            landmark8Y = landmark8?.second ?: 0f,
            raySourceLandmark = ray?.sourceLandmark ?: IndexPoint.INDEX_TIP,
            ray3D = output.ray3D,
            hit3D = output.hit3D,
            lockProvenance = output.lockProvenance,
            lockedObjectId = output.lockedObjectId,
            intersectedObjectId = output.intersectedObjectId,
            frameId = sequence,
            depthFrameId = sequence,
        )
    }
}
