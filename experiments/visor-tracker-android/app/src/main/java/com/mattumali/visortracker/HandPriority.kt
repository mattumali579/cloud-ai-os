package com.mattumali.visortracker

import kotlin.math.max
import kotlin.math.min

/**
 * Keeps MediaPipe's hand result authoritative when EfficientDet mistakes the
 * same hand-sized region for a person. Full-body person detections remain.
 */
object HandPriority {
    private const val MIN_HAND_COVERAGE = 0.55f
    private const val MAX_PERSON_TO_HAND_AREA = 12f

    fun apply(observations: List<BoxObservation>): List<BoxObservation> {
        val hands = observations.filter { hand ->
            hand.kind == "hand" && !hand.held && hand.points.size >= 21 && hand.area() > 0f
        }
        if (hands.isEmpty()) return observations
        return observations.filterNot { candidate ->
            candidate.kind == "object" && candidate.label.equals("person", ignoreCase = true) &&
                hands.any { hand -> sameHandRegion(candidate, hand) }
        }
    }

    fun label(hand: BoxObservation, frameWidth: Int, frameHeight: Int): String =
        if (IndexPoint.extended(hand, frameWidth, frameHeight) != null) "pointing hand" else "hand"

    private fun sameHandRegion(person: BoxObservation, hand: BoxObservation): Boolean {
        val intersectionWidth = max(0f, min(person.right, hand.right) - max(person.left, hand.left))
        val intersectionHeight = max(0f, min(person.bottom, hand.bottom) - max(person.top, hand.top))
        val handArea = hand.area()
        if (handArea <= 0f) return false
        val handCoverage = intersectionWidth * intersectionHeight / handArea
        val areaRatio = person.area() / handArea
        return handCoverage >= MIN_HAND_COVERAGE && areaRatio <= MAX_PERSON_TO_HAND_AREA
    }
}

/** MediaPipe's canonical 21-point hand skeleton. */
object HandSkeleton {
    val connections: List<Pair<Int, Int>> = listOf(
        0 to 1, 1 to 2, 2 to 3, 3 to 4,
        0 to 5, 5 to 6, 6 to 7, 7 to 8,
        5 to 9, 9 to 10, 10 to 11, 11 to 12,
        9 to 13, 13 to 14, 14 to 15, 15 to 16,
        13 to 17, 17 to 18, 18 to 19, 19 to 20,
        0 to 17,
    )
}
