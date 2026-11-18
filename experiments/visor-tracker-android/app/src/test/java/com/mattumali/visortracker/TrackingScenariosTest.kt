package com.mattumali.visortracker

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Synthetic frames only. Nothing here is a Galaxy S22 Ultra, Visor Live View,
 * or a phone FPS number. A 5-minute thermal soak needs the phone.
 */
class TrackingScenariosTest {
    @Test fun oneFingerLocksTheOneObject() {
        val aim = AimLock()
        val picture = frame(objectBox(2, 120f, 30f, 170f, 70f), pointingHand(90f, 50f))
        assertNull(aim.observe(picture).lockedId)
        val locked = aim.observe(picture)
        assertEquals(2, locked.lockedId)
        assertTrue(aim.fingerLocked)
    }

    @Test fun severalObjectsFollowTheRayInsteadOfTheCenter() {
        val aim = AimLock()
        val center = objectBox(1, 70f, 70f, 130f, 130f, 0.95f)
        val pointed = objectBox(2, 120f, 30f, 170f, 70f, 0.8f)
        val other = objectBox(3, 0f, 0f, 30f, 30f, 0.99f)
        val picture = frame(center, other, pointed, pointingHand(90f, 50f))
        aim.observe(picture)
        val locked = aim.observe(picture)
        assertEquals(2, locked.lockedId)
        assertFalse(locked.lockedId == 1)
    }

    @Test fun fastFingerMovementDoesNotSwitchInOneFrame() {
        val aim = AimLock()
        val first = objectBox(2, 120f, 30f, 170f, 70f)
        val start = frame(first, pointingHand(90f, 50f))
        aim.observe(start)
        assertEquals(2, aim.observe(start).lockedId)
        val other = objectBox(4, 120f, 120f, 170f, 160f)
        val moved = frame(first, other, pointingHand(90f, 140f))
        val once = aim.observe(moved)
        assertEquals(2, once.lockedId)
        assertFalse(once.changed)
    }

    @Test fun movingTargetKeepsItsTrackId() {
        val tracks = TrackIds()
        val start = tracks.update(listOf(BoxObservation("object", "cup", 0.9f, 30f, 30f, 50f, 50f)))
        val id = start.first { !it.held }.trackId
        val moved = tracks.update(listOf(BoxObservation("object", "cup", 0.9f, 34f, 30f, 54f, 50f)))
        assertEquals(id, moved.first { !it.held }.trackId)
    }

    @Test fun occludedHandKeepsTheFingerLock() {
        val aim = AimLock()
        val pointed = objectBox(2, 120f, 30f, 170f, 70f)
        val center = objectBox(1, 70f, 70f, 130f, 130f, 0.95f)
        val picture = frame(center, pointed, pointingHand(90f, 50f))
        aim.observe(picture)
        assertEquals(2, aim.observe(picture).lockedId)
        val handDown = frame(center, pointed)
        repeat(4) { assertEquals(2, aim.observe(handDown).lockedId) }
        assertTrue(aim.fingerLocked)
    }

    @Test fun knownReplacementIsAcquiredOnTheNextPublishedFrame() {
        val aim = AimLock()
        val pointed = objectBox(2, 120f, 30f, 170f, 70f, 0.8f)
        val other = objectBox(7, 10f, 10f, 40f, 40f, 0.7f)
        val picture = frame(other, pointed, pointingHand(90f, 50f))
        aim.observe(picture)
        assertEquals(2, aim.observe(picture).lockedId)
        val frameMs = 33L
        val knownAt = 8_000L
        val gone = frame(other).copy(sequence = 10, capturedAtMs = knownAt)
        assertEquals(2, aim.observe(gone).lockedId)
        val switchedAt = knownAt + frameMs
        val acquired = aim.observe(gone.copy(sequence = 11, capturedAtMs = switchedAt))
        assertEquals(7, acquired.lockedId)
        assertTrue(acquired.changed)
        val latency = switchedAt - knownAt
        assertEquals(33L, latency)
        assertTrue(latency < 500L)
    }

    @Test fun targetSwitchAndReacquisitionKeepTheShortOcclusionHold() {
        val aim = AimLock()
        val first = objectBox(1, 40f, 40f, 70f, 70f)
        aim.observe(frame(first))
        assertEquals(1, aim.observe(frame(first)).lockedId)
        repeat(2) { assertEquals(1, aim.observe(frame()).lockedId) }
        assertEquals(1, aim.observe(frame(first)).lockedId)
        val replacement = objectBox(2, 10f, 10f, 30f, 30f, 0.9f)
        assertEquals(1, aim.observe(frame(replacement)).lockedId)
        assertEquals(2, aim.observe(frame(replacement)).lockedId)
        assertEquals(2, aim.observe(frame()).lockedId)
        assertEquals(2, aim.observe(frame()).lockedId)
        assertNull(aim.observe(frame()).lockedId)
    }

    @Test fun aBurstOfNewerFramesDropsTheOlderOnes() {
        val slot = NewestFrameSlot<Long>()
        assertNull(slot.offer(1L))
        assertEquals(1L, slot.offer(2L))
        assertEquals(2L, slot.offer(3L))
        assertEquals(3L, slot.offer(4L))
        assertEquals(3, slot.dropped)
        assertEquals(4L, slot.poll())
        assertNull(slot.poll())
        val fusion = FrameFusion()
        assertEquals(4L, fusion.offer(frame(objectBox(1, 40f, 40f, 60f, 60f)).copy(sequence = 4, capturedAtMs = 400), 420)?.sequence)
        assertEquals(20L, fusion.lastAgeMs)
        assertNull(fusion.offer(frame(objectBox(1, 40f, 40f, 60f, 60f)).copy(sequence = 2, capturedAtMs = 200), 430))
        assertEquals(1, fusion.dropped)
        assertEquals(4L, fusion.offer(frame(objectBox(1, 40f, 40f, 60f, 60f)).copy(sequence = 4, capturedAtMs = 400), 430)?.sequence)
    }

    @Test fun disconnectAndReconnectClosesThePreviousPipeline() {
        val session = TrackerSession<AutoCloseable>()
        var closes = 0
        session.attach(AutoCloseable { closes += 1 })
        val handoff = TrackingHandoff()
        session.onReconnect(handoff)
        val fusion = session.fusion
        fusion.offer(frame(objectBox(1, 40f, 40f, 60f, 60f)).copy(sequence = 5, capturedAtMs = 50), 60)
        session.attach(AutoCloseable { closes += 1 })
        assertEquals(1, closes)
        assertEquals(1L, session.fusion.offer(frame(objectBox(1, 40f, 40f, 60f, 60f)).copy(sequence = 1, capturedAtMs = 10), 12)?.sequence)
        val detached = session.detach()
        detached?.close()
        assertEquals(2, closes)
        assertNull(session.pipeline)
    }

    @Test fun failureLabelIsNotTheModelGuess() {
        val directory = File("build/failure-log-test").apply {
            deleteRecursively()
            mkdirs()
        }
        val log = FailureLog(directory)
        val picture = frame(objectBox(4, 10f, 10f, 40f, 40f), pointingHand(90f, 50f))
            .copy(sequence = 3, capturedAtMs = 3_300)
        log.remember(picture, selectedId = 4)
        val stored = log.read()
        assertEquals(4, stored!!.selectedId)
        assertNull(stored.correctId)
        assertEquals("unlabeled", stored.category)
        assertTrue(stored.landmarks.size >= 5)
        val labeled = log.labelLatest(9, "bad_switch")
        assertEquals(9, labeled!!.correctId)
        assertEquals(4, labeled.selectedId)
        assertEquals("bad_switch", labeled.category)
        val roundTrip = FailureLog.decode(FailureLog.encode(labeled))
        assertEquals(9, roundTrip.correctId)
        assertEquals(3_300L, roundTrip.frameTimestampMs)
        assertFalse(File(directory, FailureLog.FILE_NAME).readText().contains("jpg"))
    }

    @Test fun tunerAcceptsTheFasterReplacementAndRejectsWorseKnobs() {
        val start = TuneConfig(knownReplacementFrames = 4)
        val before = TrackerTuner.score(start)
        val result = TrackerTuner.tune(start)
        assertTrue(result.score > before)
        assertEquals(2, result.chosen.knownReplacementFrames)
        assertEquals(IndexPoint.MIN_SCORE, result.chosen.minHandScore)
        assertEquals(IndexPoint.RAY_HYSTERESIS, result.chosen.rayHysteresis)
        assertEquals(2, result.chosen.dwellFrames)
        assertEquals(3, result.chosen.lostFrames)
        assertEquals(1, result.chosen.objectEveryLocked)
        assertTrue(result.accepted.any { it.startsWith("knownReplacementFrames=2") })
        assertTrue(result.rejected.any { it.startsWith("minHandScore=0.99") })
        assertTrue(result.rejected.any { it.startsWith("dwellFrames=1") })
        assertTrue(result.rejected.any { it.startsWith("lostFrames=1") })
        assertTrue(result.rejected.any { it.startsWith("rayHysteresis=1.01") })
        assertTrue(result.rejected.any { it.startsWith("objectEveryLocked=3") })
        assertEquals(GestureGate.DEFAULT_RELEASE_FRAMES, result.chosen.releaseFrames)
        assertTrue(result.rejected.any { it.startsWith("releaseFrames=1") })
        assertTrue(result.rejected.any { it.startsWith("releaseFrames=8") })
        assertEquals(TrackerTuner.score(TuneConfig()), result.score)
    }

    private fun frame(vararg boxes: BoxObservation) = FrameDetections(200, 200, boxes.toList())

    private fun objectBox(
        id: Int,
        left: Float,
        top: Float,
        right: Float,
        bottom: Float,
        score: Float = 0.8f,
    ) = BoxObservation("object", "object", score, left, top, right, bottom, trackId = id)
}
