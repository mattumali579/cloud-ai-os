package com.mattumali.visortracker

/**
 * The same association, gesture, and lock steps the overlay uses, with no
 * camera. Replay feeds detector boxes in and reads decisions out.
 */
class ProductionTracker {
    private val tracks = TrackIds()
    private val aim = AimLock()

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
    )

    fun step(detections: List<BoxObservation>, width: Int, height: Int): Step {
        val arm = detections.firstOrNull { HandPriority.wearerArm(it, width, height) }
        val marked = if (arm != null) detections + HandTrack.armMarker(arm) else detections
        val identified = tracks.update(HandPriority.apply(marked, width, height))
            .filter { box -> !box.held || box.kind == "hand" }
        val frame = FrameDetections(width, height, identified)
        val state = GestureStates.classify(frame)
        val decision = aim.observe(frame)
        val aimed = if (state == GestureState.POINTING_CONFIRMED) {
            IndexPoint.aimed(frame, preferId = decision.lockedId)
        } else {
            null
        }
        val ray = aimed?.ray
        return Step(
            state,
            decision.lockedId,
            aimed?.hit?.trackId,
            ray != null,
            ray?.tipX ?: 0f,
            ray?.tipY ?: 0f,
            ray?.dirX ?: 0f,
            ray?.dirY ?: 0f,
            ray?.reach ?: 0f,
            identified,
        )
    }
}
