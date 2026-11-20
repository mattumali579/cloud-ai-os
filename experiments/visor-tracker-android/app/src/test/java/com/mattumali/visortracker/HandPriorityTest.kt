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

    @Test fun extendedIndexIsNamedPointingHand() {
        val hand = pointingHand(150f, 100f)
        assertEquals("pointing hand", HandPriority.label(hand, 300, 200))
        assertEquals(21, HandSkeleton.connections.size)
    }
}
