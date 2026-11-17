package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Deterministic stand-in for the target-lock contract.
 * Timestamps are supplied by the test. Nothing here is a Galaxy S22 Ultra,
 * Visor Live View, or glasses-to-screen measurement.
 */
class LockPerformanceTest {
    @Test fun lockedDetectorStaysInsideTwoToFiveHertz() {
        assertEquals(200L, InferenceCadence.intervalMs(locked = false))
        assertEquals(250L, InferenceCadence.intervalMs(locked = true))
        assertTrue(1_000L / InferenceCadence.SEEK_MS in 2L..5L)
        assertTrue(1_000L / InferenceCadence.LOCKED_MS in 2L..5L)
        assertTrue(InferenceCadence.due(1_000, lastOfferedMs = 0, locked = true))
        assertFalse(InferenceCadence.due(1_100, lastOfferedMs = 1_000, locked = true))
        assertTrue(InferenceCadence.due(1_250, lastOfferedMs = 1_000, locked = true))
        assertFalse(InferenceCadence.due(1_100, lastOfferedMs = 1_000, locked = false))
        assertTrue(InferenceCadence.due(1_200, lastOfferedMs = 1_000, locked = false))
    }

    @Test fun fullRate30FpsMeetsIntervalAgeAndCoverageTargets() {
        val handoff = armed()
        val startLeft = 30f
        var lastLeft = startLeft
        var id = 3
        // 2 px per frame stays outside the 1 px stationary deadzone and inside the image.
        for (i in 1..20) {
            val capture = i * 33L
            val frame = gray(i * 2, sequence = 100 + i.toLong(), capture)
            handoff.offer(frame)
            val event = handoff.pump(nowMs = capture + 8, detection = null, lockedId = 3)
            assertNotNull(event)
            assertFalse(event!!.lost)
            val box = event.frame.boxes.single()
            id = box.trackId
            lastLeft = box.left
        }
        val snap = handoff.metrics.snapshotAndReset()
        println(
            "synthetic 33ms cadence n=20: decoded=${snap.decoded} processed=${snap.processed} " +
                "fraction=${snap.processedFraction()} i=${snap.intervalP50}/${snap.intervalP95}/${snap.intervalP99} " +
                "a=${snap.ageP50}/${snap.ageP95}/${snap.ageP99} drop=${snap.dropped} " +
                "jit=${snap.jitterP95Fraction} id=${snap.idSwitches}"
        )
        assertEquals(3, id)
        assertTrue(lastLeft > startLeft + 20f)
        assertTrue(snap.processedFraction() >= 0.90f)
        assertTrue(snap.intervalP95 in 1..50)
        assertTrue(snap.intervalP50 in 1..50)
        assertTrue(snap.intervalP99 in 1..100)
        assertTrue(snap.ageP95 in 0..50)
        assertTrue(snap.ageP99 in 0..100)
        assertEquals(0, snap.dropped)
        assertEquals(0, snap.idSwitches)
        assertEquals(0, snap.lockLosses)
    }

    @Test fun stationaryLockHoldsUnderOnePercentJitter() {
        val handoff = armed()
        var left = 30f
        for (i in 1..30) {
            val capture = 10_000L + i * 33L
            handoff.offer(gray(0, sequence = 200 + i.toLong(), capture))
            val event = handoff.pump(capture + 6, detection = null, lockedId = 3)
            assertNotNull(event)
            left = event!!.frame.boxes.single().left
            assertEquals(3, event.frame.boxes.single().trackId)
        }
        val snap = handoff.metrics.snapshotAndReset()
        println("synthetic stationary jit=${snap.jitterP95Fraction} left=$left losses=${snap.lockLosses}")
        assertEquals(30f, left, 0.01f)
        assertTrue(snap.jitterP95Fraction >= 0f)
        assertTrue(snap.jitterP95Fraction <= 0.01f)
        assertEquals(0, snap.idSwitches)
        assertEquals(0, snap.lockLosses)
    }

    @Test fun skippedFramesFailThe50msSpacingTarget() {
        val handoff = armed()
        var published = 0L
        for (i in 1..10) {
            val capture = 20_000L + i * 33L
            handoff.offer(gray(0, sequence = 300 + i.toLong(), capture))
            if (i % 2 == 0) {
                val event = handoff.pump(capture + 5, detection = null, lockedId = 3)
                assertNotNull(event)
                published = event!!.frame.sequence
            }
        }
        val snap = handoff.metrics.snapshotAndReset()
        assertEquals(310L, published)
        assertEquals(5, snap.processed)
        assertEquals(5, snap.dropped)
        assertTrue(snap.processedFraction() < 0.90f)
        assertTrue(snap.intervalP95 > 50)
    }

    @Test fun staleDetectionDoesNotMoveTheLockedBox() {
        val handoff = armed()
        repeat(10) { index ->
            val capture = 30_000L + index * 33L
            handoff.offer(gray(0, sequence = 400 + index.toLong(), capture))
            handoff.pump(capture + 4, detection = null, lockedId = 3)
        }
        val fresh = gray(0, sequence = 500, 31_000)
        handoff.offer(fresh)
        val impostor = BoxObservation("object", "laptop", 0.95f, 0f, 30f, 20f, 50f, trackId = 3)
        val event = handoff.pump(
            nowMs = 31_010,
            detection = DetectionBaseline(
                FrameDetections(100, 80, listOf(impostor), sequence = 1, capturedAtMs = 1_000),
                gray(0, sequence = 1, 1_000),
            ),
            lockedId = 3,
        )
        assertNotNull(event)
        assertEquals(3, event!!.frame.boxes.single().trackId)
        assertEquals(30f, event.frame.boxes.single().left, 1.1f)
        assertTrue(handoff.metrics.snapshotAndReset().staleDetections >= 1)
    }

    @Test fun anotherTrackDoesNotInheritTheLock() {
        val handoff = armed()
        val frame = gray(0, sequence = 600, 40_000)
        handoff.offer(frame)
        val other = BoxObservation("object", "cup", 0.9f, 5f, 30f, 25f, 50f, trackId = 9)
        val event = handoff.pump(
            nowMs = 40_008,
            detection = DetectionBaseline(
                FrameDetections(100, 80, listOf(other), sequence = 600, capturedAtMs = 40_000),
                frame,
            ),
            lockedId = 3,
        )
        assertNotNull(event)
        assertEquals(3, event!!.frame.boxes.single().trackId)
        assertEquals(0, handoff.metrics.snapshotAndReset().idSwitches)
    }

    @Test fun blurHidesTheBoxAndTheSameIdReturnsInside250ms() {
        val handoff = armed()
        val lostAt = 50_033L
        handoff.offer(flat(sequence = 700, lostAt))
        val hidden = handoff.pump(lostAt + 5, detection = null, lockedId = 3)
        assertNotNull(hidden)
        assertTrue(hidden!!.lost)
        assertTrue(hidden.frame.boxes.isEmpty())
        val backAt = lostAt + 40
        handoff.offer(gray(8, sequence = 701, backAt))
        val back = handoff.pump(backAt + 5, detection = null, lockedId = 3)
        assertNotNull(back)
        assertFalse(back!!.lost)
        assertEquals(3, back.frame.boxes.single().trackId)
        assertEquals(38f, back.frame.boxes.single().left, 1.5f)
        val snap = handoff.metrics.snapshotAndReset()
        assertEquals(1, snap.lockLosses)
        assertTrue(snap.reacquireP95Ms in 0..250)
    }

    @Test fun idChangeIsCountedSeparatelyFromAStableLock() {
        val metrics = LockMetrics()
        metrics.onPublished(100, 110, 40f, 100, lockedId = 3, publishedId = 3)
        metrics.onPublished(133, 140, 42f, 100, lockedId = 3, publishedId = 3)
        metrics.onPublished(166, 180, 70f, 100, lockedId = 3, publishedId = 9)
        assertEquals(1, metrics.snapshotAndReset().idSwitches)
    }

    private fun armed(): TrackingHandoff {
        val handoff = TrackingHandoff()
        val first = gray(0, sequence = 1, capture = 1_000)
        handoff.offer(first)
        val box = BoxObservation("object", "chair", 0.91f, 30f, 30f, 50f, 50f, trackId = 3)
        val event = handoff.pump(
            nowMs = 1_008,
            detection = DetectionBaseline(
                FrameDetections(100, 80, listOf(box), sequence = 1, capturedAtMs = 1_000),
                first,
            ),
            lockedId = 3,
        )
        assertNotNull(event)
        assertEquals(3, event!!.frame.boxes.single().trackId)
        handoff.metrics.snapshotAndReset()
        return handoff
    }

    private fun gray(dx: Int, sequence: Long, capture: Long): GrayFrame {
        val width = 100
        val height = 80
        val pixels = ByteArray(width * height) { 25 }
        val shift = dx.coerceAtLeast(0)
        for (y in 30 until 50) {
            for (x in 30 + shift until 50 + shift) {
                if (x >= width) continue
                pixels[y * width + x] = (65 + ((x - shift) * 37 + y * 19 + (x - shift) * (y + 11) * 7) % 175).toByte()
            }
        }
        return GrayFrame(width, height, pixels, sequence, capture)
    }

    private fun flat(sequence: Long, capture: Long): GrayFrame =
        GrayFrame(100, 80, ByteArray(100 * 80) { 40 }, sequence, capture)
}
