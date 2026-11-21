package com.mattumali.visortracker

import kotlin.math.max
import kotlin.math.min

/**
 * Keeps MediaPipe's hand result authoritative when EfficientDet mistakes the
 * same hand-sized region for a person or another ordinary object. Full-body
 * person detections and nearby objects that are not the hand remain.
 */
object HandPriority {
    private const val MIN_HAND_COVERAGE = 0.55f
    private const val MAX_OBJECT_TO_HAND_AREA = 12f

    fun apply(
        observations: List<BoxObservation>,
        frameWidth: Int = 0,
        frameHeight: Int = 0,
    ): List<BoxObservation> {
        val hands = observations.filter { hand ->
            hand.kind == "hand" && hand.points.size >= 21 && hand.area() > 0f
        }
        return observations.filterNot { candidate ->
            candidate.kind == "object" && (
                wearerArm(candidate, frameWidth, frameHeight) ||
                    (hands.isNotEmpty() && hands.any { hand ->
                        sameHandRegion(candidate, hand) || armLabeledPerson(candidate, hand)
                    })
                )
        }
    }

    /** True when EfficientDet still sees the glasses wearer's arm, even with no landmarks. */
    fun wearerArmPresent(observations: List<BoxObservation>, frameWidth: Int, frameHeight: Int): Boolean =
        observations.any { wearerArm(it, frameWidth, frameHeight) }

    fun label(hand: BoxObservation, frameWidth: Int, frameHeight: Int): String =
        if (IndexPoint.extended(hand, frameWidth, frameHeight) != null) "pointing hand" else "hand"

    private fun sameHandRegion(person: BoxObservation, hand: BoxObservation): Boolean {
        val neverHand = person.label.lowercase() in setOf(
            "laptop", "tv", "bed", "couch", "chair", "dining table", "refrigerator", "bench", "table", "book", "clock", "airplane", "suitcase", "backpack", "handbag"
        )
        if (neverHand) return false

        val intersectionWidth = max(0f, min(person.right, hand.right) - max(person.left, hand.left))
        val intersectionHeight = max(0f, min(person.bottom, hand.bottom) - max(person.top, hand.top))
        val handArea = hand.area()
        if (handArea <= 0f) return false
        val handCoverage = intersectionWidth * intersectionHeight / handArea
        val areaRatio = person.area() / handArea
        return handCoverage >= MIN_HAND_COVERAGE && areaRatio <= MAX_OBJECT_TO_HAND_AREA
    }

    /**
     * EfficientDet often labels a forearm as "person". A walking figure stays:
     * it is tall relative to the hand. A long arm box still contains the wrist
     * even when its center is far from the palm.
     */
    private fun armLabeledPerson(person: BoxObservation, hand: BoxObservation): Boolean {
        if (!person.label.equals("person", ignoreCase = true)) return false
        val wrist = hand.points.getOrNull(IndexPoint.WRIST) ?: return false
        if (wrist.first < person.left || wrist.first > person.right ||
            wrist.second < person.top || wrist.second > person.bottom
        ) {
            return false
        }
        val personH = person.bottom - person.top
        val personW = person.right - person.left
        val span = max(hand.right - hand.left, hand.bottom - hand.top).coerceAtLeast(24f)
        val looksLikeBody = personH > personW * 1.6f &&
            personH > span * 6f &&
            person.area() > hand.area() * 20f
        return !looksLikeBody
    }

    /**
     * The wearer's forearm enters from a frame edge. A standing person in the
     * scene starts near the top of the picture and is much taller than wide.
     */
    internal fun wearerArm(person: BoxObservation, frameWidth: Int, frameHeight: Int): Boolean {
        if (frameWidth <= 0 || frameHeight <= 0) return false
        val label = person.label.lowercase()
        val limbLabel = label == "person" || label == "object" ||
            label == "teddy bear" || label == "dog" || label == "cat" || label == "bear"
        if (!limbLabel) return false
        val margin = max(12f, min(frameWidth, frameHeight) * 0.03f)
        val fromLeft = person.left <= margin
        val fromRight = person.right >= frameWidth - margin
        val fromBottom = person.bottom >= frameHeight - margin
        if (!fromLeft && !fromRight && !fromBottom) return false
        val boxH = person.bottom - person.top
        val boxW = person.right - person.left
        val frameArea = frameWidth.toFloat() * frameHeight.toFloat()
        val frac = person.area() / frameArea
        if (label != "person") {
            if (frac < 0.04f || frac > 0.35f) return false
            if (boxH < boxW * 1.05f) return false
        }
        val standingFigure = boxH > boxW * 1.8f &&
            person.top < frameHeight * 0.18f &&
            boxH > frameHeight * 0.50f
        return !standingFigure
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
