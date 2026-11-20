package com.mattumali.visortracker

/**
 * Live HUD copied from the owner's training stills: 21 landmarks, landmark 8
 * as the pointer, pointing vs fist, and the locked object id such as cup #766.
 */
object TrainingHud {
    const val POINTING = "pointing hand"
    const val HAND = "hand"

    data class Status(
        val hands: Int,
        val landmarks: Int,
        val pointing: Boolean,
        val gesture: String,
        val targetLabel: String?,
        val targetId: Int?,
        val indexX: Float?,
        val indexY: Float?,
        val score: Float?,
    )

    fun status(frame: FrameDetections, lockedId: Int?): Status {
        val hands = frame.boxes.filter { it.kind == "hand" && it.points.size >= 21 }
        val primary = hands.maxByOrNull { it.score }
        val ray = IndexPoint.ray(frame)
        val pointing = ray != null
        val aimed = if (ray == null) {
            null
        } else {
            IndexPoint.alongRay(ray, frame.boxes, lockedId)
        }
        val locked = lockedId?.let { id ->
            frame.boxes.firstOrNull { it.kind == "object" && it.trackId == id }
        }
        val target = if (pointing) aimed ?: locked else null
        val tip = primary?.let { HandTrack.indexTip(it) }
        return Status(
            hands = hands.size,
            landmarks = primary?.points?.size ?: 0,
            pointing = pointing,
            gesture = if (pointing) POINTING else HAND,
            targetLabel = target?.label,
            targetId = target?.trackId,
            indexX = tip?.first,
            indexY = tip?.second,
            score = primary?.score,
        )
    }

    fun lines(status: Status, decoderFps: Float = 0f): List<String> {
        val target = if (status.targetLabel != null && status.targetId != null) {
            "${status.targetLabel} #${status.targetId}"
        } else {
            "none"
        }
        val fps = if (decoderFps > 0f) "%.0f".format(java.util.Locale.US, decoderFps) else "--"
        val conf = status.score?.let { "%.2f".format(java.util.Locale.US, it) } ?: "--"
        return listOf(
            "Hands: ${status.hands}",
            "LMK: ${status.landmarks}",
            "State: ${status.gesture}",
            "Target: $target",
            "Conf: $conf",
            "FPS: $fps",
        )
    }
}
