package com.mattumali.visortracker

/**
 * Draws one object.
 *
 * Before a lock, the box under the center of the picture is drawn. After a
 * lock, that track is drawn while it is still in the frame. If the locked id
 * is missing, the pointed object is drawn instead of an empty overlay.
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
            !it.held && it.kind == "object" && it.trackId > 0 &&
                it.right > it.left && it.bottom > it.top
        }
        val locked = if (lockedId == null) null else objects.firstOrNull { it.trackId == lockedId }
        val chosen = locked ?: pointedObject(frameWidth, frameHeight, objects)
        return if (chosen == null) emptyList() else listOf(chosen).take(limit)
    }

    companion object {
        const val DISPLAY_LIMIT = 1
        const val CAPTION = "one object"
    }
}
