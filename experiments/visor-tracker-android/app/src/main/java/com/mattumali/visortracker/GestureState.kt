package com.mattumali.visortracker

/**
 * What the camera actually showed. An arm box is not a finger, and an open
 * hand is not a point. Only [POINTING_CONFIRMED] may acquire a lock.
 */
enum class GestureState {
    NONE,
    ARM_VISIBLE,
    HAND_VISIBLE,
    POINTING_CONFIRMED,
}

object GestureStates {
    fun classify(frame: FrameDetections): GestureState {
        val hands = frame.boxes.filter { box ->
            box.kind == "hand" && !box.synthetic && !box.held && box.points.size >= 21
        }
        if (hands.any { IndexPoint.extended(it, frame.width, frame.height) != null }) {
            return GestureState.POINTING_CONFIRMED
        }
        if (hands.isNotEmpty()) return GestureState.HAND_VISIBLE
        if (frame.boxes.any { it.kind == "arm" }) return GestureState.ARM_VISIBLE
        return GestureState.NONE
    }
}
