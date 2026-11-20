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
    private var lastId: Int? = null

    fun select(
        frameWidth: Int,
        frameHeight: Int,
        boxes: List<BoxObservation>,
        lockedId: Int?,
        aimTrackId: Int? = null,
    ): List<BoxObservation> {
        if (frameWidth <= 0 || frameHeight <= 0 || limit <= 0) return emptyList()
        val objects = boxes.filter {
            IndexPoint.pointable(it, frameWidth, frameHeight)
        }
        val locked = if (lockedId == null) null else objects.firstOrNull { it.trackId == lockedId }
        val aimed = if (aimTrackId == null) null else objects.firstOrNull { it.trackId == aimTrackId }
        val sticky = lastId?.let { id -> objects.firstOrNull { it.trackId == id } }
        val chosen = locked
            ?: aimed?.takeIf { lastId == null || lastId == aimed.trackId }
            ?: sticky
            ?: aimed
            ?: pointedObject(frameWidth, frameHeight, objects)
        lastId = chosen?.trackId
        return if (chosen == null) emptyList() else listOf(chosen).take(limit)
    }

    companion object {
        const val DISPLAY_LIMIT = 1
        const val CAPTION = "one object"
    }
}
