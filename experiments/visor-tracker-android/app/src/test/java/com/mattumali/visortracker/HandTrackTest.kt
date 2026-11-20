package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class HandTrackTest {
    @Test fun indexTipIsTheIdentityReferenceAcrossAJump() {
        val tracker = TrackIds()
        val start = pointingHand(40f, 80f)
        val first = tracker.update(listOf(start)).first { it.kind == "hand" && !it.held }
        val jumped = pointingHand(140f, 30f)
        val next = tracker.update(listOf(jumped)).first { it.kind == "hand" && !it.held }
        assertEquals(first.trackId, next.trackId)
        assertEquals(140f, HandTrack.indexTip(next)!!.first, 0.1f)
        assertTrue(HandTrack.matchScore(start.copy(trackId = first.trackId), jumped) > 0f)
    }

    @Test fun aBriefDisappearanceKeepsTheSameHand() {
        val tracker = TrackIds()
        val first = tracker.update(listOf(pointingHand(90f, 50f))).first { it.kind == "hand" && !it.held }
        var heldId = 0
        repeat(HandTrack.HAND_GRACE_FRAMES) {
            val held = tracker.update(emptyList()).first { it.kind == "hand" }
            assertTrue(held.held)
            heldId = held.trackId
        }
        assertEquals(first.trackId, heldId)
        val recovered = tracker.update(listOf(pointingHand(100f, 55f)))
            .first { it.kind == "hand" && !it.held }
        assertEquals(first.trackId, recovered.trackId)
    }

    @Test fun wristKeepsIdentityWhenLandmark8JumpsOffTheFinger() {
        val start = pointingHand(40f, 80f)
        val wrongTip = pointingHand(48f, 80f).let { hand ->
            val points = hand.points.toMutableList()
            points[IndexPoint.INDEX_TIP] = 4f to 4f
            hand.copy(points = points)
        }
        assertTrue(HandTrack.matchScore(start, wrongTip) > 0f)
    }

    @Test fun hullOutlinesTheWholeHand() {
        val hull = HandOutline.hull(pointingHand(90f, 50f).points)
        assertTrue(hull.size >= 3)
    }

    @Test fun pinToArmSlidesTheWristIntoTheArmBox() {
        val hand = pointingHand(150f, 100f)
        val arm = BoxObservation("object", "person", 0.9f, 200f, 80f, 280f, 140f, trackId = 3)
        val pinned = HandTrack.pinToArm(hand, arm)
        val wrist = pinned.points[IndexPoint.WRIST]
        assertTrue(wrist.first in 200f..280f)
        assertTrue(wrist.second in 80f..140f)
    }

    @Test fun displayOmitsAHeldSkeletonAfterTheHandLeaves() {
        val live = pointingHand(90f, 50f).copy(trackId = 7)
        val held = pointingHand(40f, 40f).copy(trackId = 7, held = true, score = 0.5f)
        assertEquals(7, HandTrack.forDisplay(listOf(live)).single().trackId)
        assertTrue(HandTrack.forDisplay(listOf(held)).isEmpty())
        assertTrue(HandTrack.forDisplay(listOf(live, held)).none { it.held })
        assertTrue(HandTrack.compose(emptyList(), listOf(live)).any { it.kind == "hand" })
    }
}
