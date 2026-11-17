package com.mattumali.visortracker

import kotlin.math.max
import kotlin.math.min

data class BoxObservation(
    val kind: String,
    val label: String,
    val score: Float,
    val left: Float,
    val top: Float,
    val right: Float,
    val bottom: Float,
    val points: List<Pair<Float, Float>> = emptyList(),
    var trackId: Int = 0
) {
    fun area(): Float = max(0f, right - left) * max(0f, bottom - top)
}

/**
 * Associates nearby boxes with a persistent identity, even when the model
 * briefly mistakes one object category for another.
 *
 * Requires strong overlap to relabel an object, holds its previous category
 * until repeated agreement, and retains a missing detection briefly. These
 * rules stabilize visualization; they do not create new model categories.
 */
class TrackIds(
    private val matchThreshold: Float = 0.25f,
    private val maxMissedFrames: Int = 8,
    private val graceFrames: Int = 2,
    private val relabelConfirmations: Int = 3
) {
    private data class Track(
        val id: Int,
        val kind: String,
        var label: String,
        var box: BoxObservation,
        var missed: Int = 0,
        var pendingLabel: String? = null,
        var pendingCount: Int = 0
    )

    private val tracks = mutableListOf<Track>()
    private var nextId = 1

    fun update(observations: List<BoxObservation>): List<BoxObservation> {
        tracks.forEach { it.missed++ }
        // Candidate scoring favors the existing category; a high-overlap
        // category change can still retain the physical track.
        val candidates = mutableListOf<Triple<Float, Track, Int>>()
        for (track in tracks) {
            observations.forEachIndexed { index, observation ->
                if (track.kind != observation.kind) return@forEachIndexed
                val overlap = iou(track.box, observation)
                val sameLabel = track.label == observation.label
                val threshold = if (sameLabel) matchThreshold else 0.55f
                if (overlap >= threshold) {
                    candidates += Triple(overlap + if (sameLabel) 0.12f else 0f, track, index)
                }
            }
        }

        val matchedTracks = mutableSetOf<Int>()
        val matchedObservationIndexes = mutableSetOf<Int>()
        val rendered = arrayOfNulls<BoxObservation>(observations.size)
        candidates.sortedByDescending { it.first }.forEach { (_, track, index) ->
            if (track.id in matchedTracks || index in matchedObservationIndexes) return@forEach
            matchedTracks += track.id
            matchedObservationIndexes += index
            val observation = observations[index]
            // "object" is missing category evidence from LabelPolicy, not a new
            // name. It must not accumulate toward a rename, and it must not
            // replace a specific label such as "cell phone".
            if (track.label == observation.label || observation.label == LabelPolicy.UNCERTAIN) {
                track.pendingLabel = null
                track.pendingCount = 0
            } else {
                if (track.pendingLabel == observation.label) {
                    track.pendingCount++
                } else {
                    track.pendingLabel = observation.label
                    track.pendingCount = 1
                }
                if (track.pendingCount >= relabelConfirmations) {
                    track.label = observation.label
                    track.pendingLabel = null
                    track.pendingCount = 0
                }
            }
            track.missed = 0
            observation.trackId = track.id
            track.box = observation.copy(label = track.label, trackId = track.id)
            rendered[index] = track.box.copy()
        }

        observations.forEachIndexed { index, observation ->
            if (index !in matchedObservationIndexes) {
                observation.trackId = nextId++
                val copy = observation.copy()
                tracks += Track(copy.trackId, copy.kind, copy.label, copy)
                rendered[index] = copy
            }
        }

        tracks.removeAll { it.missed > maxMissedFrames }
        // Keep a missed box visible for a couple of detector frames, giving
        // the grayscale motion tracker a chance to follow it until reacquired.
        val held = tracks.filter { it.missed in 1..graceFrames.coerceAtMost(maxMissedFrames) }
            .map { track ->
                track.box.copy(score = track.box.score * if (track.missed == 1) 0.8f else 0.6f)
            }
        return rendered.filterNotNull() + held
    }

    private fun iou(a: BoxObservation, b: BoxObservation): Float {
        val width = max(0f, min(a.right, b.right) - max(a.left, b.left))
        val height = max(0f, min(a.bottom, b.bottom) - max(a.top, b.top))
        val intersection = width * height
        val union = a.area() + b.area() - intersection
        return if (union <= 0f) 0f else intersection / union
    }
}
