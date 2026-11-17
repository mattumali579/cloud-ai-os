package com.mattumali.visortracker

/**
 * Picks at most [DISPLAY_LIMIT] boxes to draw.
 *
 * A single calibrated Meta camera cannot measure meters for five objects of
 * different real sizes. Ranking is a visual estimate: lower in the frame is
 * treated as more likely to be near the wearer (ground-plane prior), and
 * apparent size is compared only inside the same label. Distance from the
 * frame center is not used. The caption [CAPTION] must stay visible so the
 * overlay does not claim measured closeness.
 *
 * Membership is sticky. A newcomer replaces the weakest visible track only
 * after it leads by [replaceMargin] for [promoteFrames] consecutive frames.
 * The calibrated track stays inside the cap whenever it is still a live object.
 * An uncertain name with a weak score is not used to fill empty slots.
 * The object nearest the frame center is kept so calibration still has a target.
 * Hands and faces use leftover slots only, so they cannot push objects out.
 */
class NearbyCandidates(
    private val limit: Int = DISPLAY_LIMIT,
    private val promoteFrames: Int = 3,
    private val replaceMargin: Float = 0.05f,
) {
    private val scoreById = HashMap<Int, Float>()
    private val shown = LinkedHashSet<Int>()
    private val challengerFrames = HashMap<Int, Int>()

    fun select(
        frameWidth: Int,
        frameHeight: Int,
        boxes: List<BoxObservation>,
        lockedId: Int?,
    ): List<BoxObservation> {
        if (frameWidth <= 0 || frameHeight <= 0 || limit <= 0) return emptyList()
        val prepared = poolForDisplay(frameWidth, frameHeight, boxes, lockedId)
        val objects = prepared.filter { it.isLiveObject() }
        val extras = prepared.filter { it.kind != "object" && it.trackId > 0 }
        val live = LinkedHashMap<Int, BoxObservation>()
        for (box in objects) live.putIfAbsent(box.trackId, box)
        val liveIds = live.keys
        scoreById.keys.retainAll(liveIds)
        challengerFrames.keys.retainAll(liveIds)
        shown.retainAll(liveIds)

        val peers = live.values.toList()
        for (box in peers) {
            val raw = visualNearness(box, frameHeight, peers)
            val previous = scoreById[box.trackId]
            scoreById[box.trackId] = if (previous == null) raw else previous * 0.65f + raw * 0.35f
        }
        val ranked = peers.sortedByDescending { scoreById[it.trackId] ?: 0f }

        if (shown.isEmpty()) {
            ranked.take(limit).forEach { shown += it.trackId }
        } else {
            for (box in ranked) {
                if (shown.size >= limit) break
                shown += box.trackId
            }
            considerReplacement(ranked, lockedId)
        }
        keepLocked(lockedId, liveIds)

        val chosen = shown.mapNotNull { live[it] }
            .sortedByDescending { scoreById[it.trackId] ?: 0f }
            .take(limit)
        val room = limit - chosen.size
        return if (room <= 0) chosen else chosen + extras.take(room)
    }

    private fun considerReplacement(ranked: List<BoxObservation>, lockedId: Int?) {
        if (shown.size < limit) return
        val weakestId = shown.filter { it != lockedId }.minByOrNull { scoreById[it] ?: 0f } ?: return
        val weakest = scoreById[weakestId] ?: 0f
        val challenger = ranked.firstOrNull { it.trackId !in shown } ?: return
        val margin = (scoreById[challenger.trackId] ?: 0f) - weakest
        if (margin < replaceMargin) {
            challengerFrames.clear()
            return
        }
        challengerFrames.keys.retainAll(setOf(challenger.trackId))
        val count = (challengerFrames[challenger.trackId] ?: 0) + 1
        challengerFrames[challenger.trackId] = count
        if (count >= promoteFrames) {
            shown.remove(weakestId)
            shown.add(challenger.trackId)
            challengerFrames.remove(challenger.trackId)
        }
    }

    private fun keepLocked(lockedId: Int?, liveIds: Set<Int>) {
        if (lockedId == null || lockedId !in liveIds) return
        if (lockedId in shown) return
        if (shown.size >= limit) {
            val drop = shown.filter { it != lockedId }.minByOrNull { scoreById[it] ?: 0f }
            if (drop != null) shown.remove(drop)
        }
        if (shown.size < limit) shown.add(lockedId)
    }

    private fun BoxObservation.isLiveObject(): Boolean =
        kind == "object" && trackId > 0 && right > left && bottom > top

    companion object {
        const val DISPLAY_LIMIT = 5
        const val CAPTION = "up to 5 visual candidates"
        /** Uncertain names below this score do not fill a slot. Not a correctness rate. */
        const val UNCERTAIN_DISPLAY_SCORE = 0.62f

        fun poolForDisplay(
            frameWidth: Int,
            frameHeight: Int,
            boxes: List<BoxObservation>,
            lockedId: Int?,
        ): List<BoxObservation> {
            if (frameWidth <= 0 || frameHeight <= 0) return boxes
            val objects = boxes.filter { it.kind == "object" && it.trackId > 0 }
            val centerId = objects.minByOrNull { box ->
                val cx = (box.left + box.right) * 0.5f / frameWidth - 0.5f
                val cy = (box.top + box.bottom) * 0.5f / frameHeight - 0.5f
                cx * cx + cy * cy
            }?.trackId
            return boxes.filter { box ->
                if (box.kind != "object") return@filter true
                if (box.trackId == lockedId || box.trackId == centerId) return@filter true
                box.label != LabelPolicy.UNCERTAIN || box.score >= UNCERTAIN_DISPLAY_SCORE
            }
        }

        fun visualNearness(box: BoxObservation, frameHeight: Int, peers: List<BoxObservation>): Float {
            val bottom = (box.bottom / frameHeight.toFloat()).coerceIn(0f, 1f)
            val sameLabel = peers.filter { it.label == box.label }
            val maxArea = sameLabel.maxOfOrNull { it.area() } ?: box.area()
            val relativeSize = if (maxArea <= 1e-3f) 0f else (box.area() / maxArea).coerceIn(0f, 1f)
            return bottom * 0.7f + relativeSize * 0.3f
        }
    }
}
