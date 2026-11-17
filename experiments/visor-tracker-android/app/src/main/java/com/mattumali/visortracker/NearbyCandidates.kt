package com.mattumali.visortracker

/**
 * Draws one object.
 *
 * Before a lock, the box nearest the center of the frame is the candidate
 * the glasses are pointing at. After a lock, only that track is drawn.
 * Other detections stay available to the point-to-lock chooser and are not painted.
 * This is not a measured-distance ranking, and it does not make inference faster.
 */
class NearbyCandidates(
    private val limit: Int = DISPLAY_LIMIT,
) {
    fun select(
        frameWidth: Int,
        frameHeight: Int,
        boxes: List<BoxObservation>,
        lockedId: Int?,
    ): List<BoxObservation> {
        if (frameWidth <= 0 || frameHeight <= 0 || limit <= 0) return emptyList()
        val objects = boxes.filter {
            it.kind == "object" && it.trackId > 0 && it.right > it.left && it.bottom > it.top
        }
        val chosen = if (lockedId != null) {
            objects.firstOrNull { it.trackId == lockedId }
        } else {
            objects.minByOrNull { box ->
                val cx = (box.left + box.right) * 0.5f / frameWidth - 0.5f
                val cy = (box.top + box.bottom) * 0.5f / frameHeight - 0.5f
                cx * cx + cy * cy
            }
        }
        return if (chosen == null) emptyList() else listOf(chosen).take(limit)
    }

    companion object {
        const val DISPLAY_LIMIT = 1
        const val CAPTION = "one object"
    }
}
