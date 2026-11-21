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
    )

    fun reset() {
        engine.reset()
    }

    fun step(detections: List<BoxObservation>, width: Int, height: Int): Step {
        val arm = detections.firstOrNull { HandPriority.wearerArm(it, width, height) }
        val marked = if (arm != null) detections + HandTrack.armMarker(arm) else detections
        val identified = tracks.update(HandPriority.apply(marked, width, height))
            .filter { box -> !box.held || box.kind == "hand" }
        val frame = FrameDetections(width, height, identified)

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
        )
    }
}
