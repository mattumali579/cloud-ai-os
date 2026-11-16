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
