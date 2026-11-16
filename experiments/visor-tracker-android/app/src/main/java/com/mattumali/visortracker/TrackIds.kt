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

class TrackIds(
    private val matchThreshold: Float = 0.25f,
    private val maxMissedFrames: Int = 8
) {
    private data class Track(
        val id: Int,
        val kind: String,
        val label: String,
        var box: BoxObservation,
        var missed: Int = 0
    )

    private val tracks = mutableListOf<Track>()
    private var nextId = 1

    fun update(observations: List<BoxObservation>): List<BoxObservation> {
        tracks.forEach { it.missed++ }
        val candidates = mutableListOf<Triple<Float, Track, Int>>()
        for (track in tracks) {
            observations.forEachIndexed { index, observation ->
                if (track.kind == observation.kind && track.label == observation.label) {
                    val overlap = iou(track.box, observation)
                    if (overlap >= matchThreshold) candidates += Triple(overlap, track, index)
                }
            }
        }

        val matchedTracks = mutableSetOf<Int>()
        val matchedObservationIndexes = mutableSetOf<Int>()
        candidates.sortedByDescending { it.first }.forEach { (_, track, index) ->
            if (matchedTracks.add(track.id) && matchedObservationIndexes.add(index)) {
                val observation = observations[index]
                track.box = observation
                track.missed = 0
                observation.trackId = track.id
            }
        }

        observations.forEachIndexed { index, observation ->
            if (index !in matchedObservationIndexes) {
                observation.trackId = nextId++
                tracks += Track(observation.trackId, observation.kind, observation.label, observation)
            }
        }
        tracks.removeAll { it.missed > maxMissedFrames }
        return observations
    }

    private fun iou(a: BoxObservation, b: BoxObservation): Float {
        val width = max(0f, min(a.right, b.right) - max(a.left, b.left))
        val height = max(0f, min(a.bottom, b.bottom) - max(a.top, b.top))
        val intersection = width * height
        val union = a.area() + b.area() - intersection
        return if (union <= 0f) 0f else intersection / union
    }
}
