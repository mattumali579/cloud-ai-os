package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Test

class TrackIdsTest {
    @Test fun keepsIdWhenBoxMovesSlightly() {
        val tracker = TrackIds()
        val first = BoxObservation("object", "cup", .9f, 10f, 10f, 60f, 60f)
        tracker.update(listOf(first))
        val moved = BoxObservation("object", "cup", .8f, 12f, 11f, 62f, 61f)
        tracker.update(listOf(moved))
        assertEquals(first.trackId, moved.trackId)
    }

    @Test fun doesNotMatchDifferentLabels() {
        val tracker = TrackIds()
        val face = BoxObservation("face", "face", .9f, 10f, 10f, 60f, 60f)
        tracker.update(listOf(face))
        val cup = BoxObservation("object", "cup", .9f, 10f, 10f, 60f, 60f)
        tracker.update(listOf(cup))
        assertEquals(false, face.trackId == cup.trackId)
    }

    @Test fun keepsIdAndLabelWhenObjectClassifierFlickers() {
        val tracker = TrackIds()
        val bottle = BoxObservation("object", "bottle", .85f, 10f, 10f, 80f, 80f)
        tracker.update(listOf(bottle))
        val mistakenCup = BoxObservation("object", "cup", .42f, 12f, 12f, 82f, 82f)
        val stable = tracker.update(listOf(mistakenCup))
        assertEquals(bottle.trackId, mistakenCup.trackId)
        assertEquals("bottle", stable.first().label)
    }

    @Test fun categoryOnlyChangesAfterThreeConsistentDetections() {
        val tracker = TrackIds()
        val first = BoxObservation("object", "bottle", .85f, 10f, 10f, 80f, 80f)
        tracker.update(listOf(first))
        for (attempt in 1..3) {
            val updated = tracker.update(listOf(
                BoxObservation("object", "cup", .9f, 11f, 11f, 81f, 81f)
            ))
            assertEquals(first.trackId, updated.first().trackId)
            assertEquals(if (attempt < 3) "bottle" else "cup", updated.first().label)
        }
    }

    @Test fun brieflyHoldsMissedBoxInsteadOfFlickeringOff() {
        val tracker = TrackIds()
        tracker.update(listOf(BoxObservation("object", "chair", .9f, 0f, 0f, 40f, 40f)))
        assertEquals("chair", tracker.update(emptyList()).single().label)
        assertEquals("chair", tracker.update(emptyList()).single().label)
        assertEquals(0, tracker.update(emptyList()).size)
    }

    @Test fun expiresTracksAfterMissWindow() {
        val tracker = TrackIds(maxMissedFrames = 1)
        val first = BoxObservation("object", "cup", .9f, 0f, 0f, 20f, 20f)
        tracker.update(listOf(first))
        tracker.update(emptyList())
        tracker.update(emptyList())
        val reappeared = BoxObservation("object", "cup", .9f, 0f, 0f, 20f, 20f)
        tracker.update(listOf(reappeared))
        assertEquals(false, first.trackId == reappeared.trackId)
    }
}
