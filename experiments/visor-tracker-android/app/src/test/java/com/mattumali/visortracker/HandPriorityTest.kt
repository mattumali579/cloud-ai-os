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

    @Test fun aPersonBoxCenteredOnTheWristIsTheArmNotAPerson() {
        val hand = pointingHand(150f, 100f)
        val arm = BoxObservation(
            "object", "person", 0.88f,
            hand.left - 20f, hand.top - 20f, hand.right + 40f, hand.bottom + 20f,
            trackId = 330,
        )
        val visible = HandPriority.apply(listOf(arm, hand))
        assertFalse(visible.any { it.label == "person" })
        assertTrue(visible.any { it.kind == "hand" })
    }

    @Test fun aLongForearmLabeledPersonIsDroppedEvenWhenItsCenterIsFarFromTheWrist() {
        val hand = pointingHand(150f, 100f)
        val forearm = BoxObservation(
            "object", "person", 0.9f,
            40f, 70f, 520f, 150f,
            trackId = 330,
        )
        val visible = HandPriority.apply(listOf(forearm, hand))
        assertFalse(visible.any { it.label == "person" })
        assertTrue(visible.any { it.kind == "hand" })
    }

    @Test fun aWearerArmIsDroppedEvenWhenMediaPipeMissesTheHand() {
        val arm = BoxObservation("object", "person", 0.9f, 80f, 90f, 200f, 200f, trackId = 102)
        val cup = BoxObservation("object", "cup", 0.8f, 10f, 10f, 40f, 50f, trackId = 7)
        val visible = HandPriority.apply(listOf(arm, cup), 200, 200)
        assertFalse(visible.any { it.label == "person" })
        assertTrue(visible.any { it.trackId == 7 })
    }

    @Test fun anUncertainEdgeArmIsNotDrawnAsAnObject() {
        val arm = BoxObservation("object", "object", 0.40f, 500f, 816f, 1080f, 1795f, trackId = 140)
        val phone = BoxObservation("object", "book", 0.9f, 620f, 1550f, 950f, 2100f, trackId = 168)
        val visible = HandPriority.apply(listOf(arm, phone), 1080, 2316)
        assertFalse(visible.any { it.trackId == 140 })
        assertTrue(visible.any { it.trackId == 168 })
    }

    @Test fun aStandingPersonInTheSceneRemainsWithoutAHand() {
        val person = BoxObservation("object", "person", 0.9f, 40f, 0f, 120f, 180f, trackId = 4)
        val visible = HandPriority.apply(listOf(person), 200, 200)
        assertTrue(visible.any { it.label == "person" })
    }

    @Test fun extendedIndexIsNamedPointingHand() {
        val hand = pointingHand(150f, 100f)
        assertEquals("pointing hand", HandPriority.label(hand, 300, 200))
        assertEquals(21, HandSkeleton.connections.size)
    }
}
