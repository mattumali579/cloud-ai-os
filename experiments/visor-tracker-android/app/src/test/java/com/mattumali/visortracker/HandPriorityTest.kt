package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HandPriorityTest {
    @Test fun overlappingHandSizedPersonDoesNotCoverHandLandmarks() {
        val hand = pointingHand(150f, 100f)
        val mistakenPerson = BoxObservation(
            "object", "person", 0.72f,
            hand.left - 8f, hand.top - 8f, hand.right + 8f, hand.bottom + 8f,
            trackId = 4,
        )

        val visible = HandPriority.apply(listOf(mistakenPerson, hand))

        assertFalse(visible.any { it.label == "person" })
        assertTrue(visible.any { it.kind == "hand" && it.points.size == 21 })
    }

    @Test fun fullBodyPersonRemainsWhenAHandIsVisible() {
        val hand = pointingHand(150f, 100f)
        val person = BoxObservation("object", "person", 0.9f, 0f, 0f, 400f, 800f, trackId = 4)

        val visible = HandPriority.apply(listOf(person, hand))

        assertTrue(visible.any { it.label == "person" })
    }

    @Test fun overlappingHandSizedObjectDoesNotCoverHandLandmarks() {
        val hand = pointingHand(150f, 100f)
        val mistaken = BoxObservation(
            "object", "cup", 0.8f,
            hand.left - 4f, hand.top - 4f, hand.right + 4f, hand.bottom + 4f,
            trackId = 9,
        )

        val visible = HandPriority.apply(listOf(mistaken, hand))

        assertTrue(visible.none { it.kind == "object" })
        assertTrue(visible.any { it.kind == "hand" && it.points.size == 21 })
    }

    @Test fun aSeparateCupRemainsBesideTheHand() {
        val hand = pointingHand(150f, 100f)
        val cup = BoxObservation("object", "cup", 0.9f, 0f, 0f, 30f, 30f, trackId = 2)

        val visible = HandPriority.apply(listOf(cup, hand))

        assertTrue(visible.any { it.trackId == 2 && it.label == "cup" })
    }

    @Test fun extendedIndexIsNamedPointingHand() {
        val hand = pointingHand(150f, 100f)
        assertEquals("pointing hand", HandPriority.label(hand, 300, 200))
        assertEquals(21, HandSkeleton.connections.size)
    }
}
