package com.mattumali.visortracker

/**
 * Draws one object.
 *
 * The box nearest the center is drawn as soon as the detector returns it.
 * After a lock, that track is drawn while it is still in the frame. If the
 * locked id is missing, the center object is drawn instead of an empty overlay.
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
        val locked = if (lockedId == null) null else objects.firstOrNull { it.trackId == lockedId }
        val chosen = locked ?: objects.minByOrNull { box ->
            val cx = (box.left + box.right) * 0.5f / frameWidth - 0.5f
            val cy = (box.top + box.bottom) * 0.5f / frameHeight - 0.5f
            cx * cx + cy * cy
        }
        return if (chosen == null) emptyList() else listOf(chosen).take(limit)
    }

    companion object {
        const val DISPLAY_LIMIT = 1
        const val CAPTION = "one object"
    }
}
