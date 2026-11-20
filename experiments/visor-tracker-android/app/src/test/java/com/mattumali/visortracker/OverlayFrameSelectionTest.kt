package com.mattumali.visortracker

import java.util.ArrayDeque
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test

/**
 * The detection that switches the lock has to publish that new id.
 * Reading the id before the update runs still sees the previous target,
 * and the handoff then draws the previous box. No device, clock, or sleep.
 */
class OverlayFrameSelectionTest {
    @Test fun switchFramePublishesTheNewLock() {
        val stale = publish(readBeforeUpdate = true)
        val current = publish(readBeforeUpdate = false)
        assertEquals(1, stale.lockId)
        assertEquals(1, stale.trackId)
        assertEquals(false, stale.updateVisible)
        assertEquals(2, current.lockId)
        assertEquals(2, current.trackId)
        assertEquals(2, current.queuedDraw)
        assertEquals(true, current.updateVisible)
    }

    @Test fun unconfirmedAimKeepsThePreviousLock() {
        val published = publish(readBeforeUpdate = false, holdsBeforeFrame = 0)
        assertEquals(1, published.lockId)
        assertEquals(1, published.trackId)
    }

    @Test fun anInterruptedWaitDoesNotHandMotionThePreviousLock() {
        val aimed = frame(side(3, 0f), aside(1), side(4, 78f), centered(2))
        val previous = 1
        // Seeding the seen id, then returning on interrupt, handed motion track 1.
        assertEquals(previous, pumpedTrack(previous, aimed))

        val fixed = MainThreadLockWait.finish(
            posted = true,
            interrupted = true,
            updateRan = false,
            idAfterUpdate = previous,
        )
        assertEquals(false, MainThreadLockWait.queueDetection(fixed))
        assertEquals(null, fixed.lockId)
    }

    @Test fun anInterruptAfterTheUpdateKeepsTheNewLock() {
        val fixed = MainThreadLockWait.finish(
            posted = true,
            interrupted = true,
            updateRan = true,
            idAfterUpdate = 2,
        )
        assertEquals(true, fixed.applied)
        assertEquals(2, fixed.lockId)
    }

    @Test fun aTimedOutLockWaitDrawsTheNewDetectionWithoutMotion() {
        val fixed = MainThreadLockWait.finish(
            posted = true,
            interrupted = false,
            updateRan = false,
            idAfterUpdate = 1,
            timedOut = true,
        )
        assertEquals(false, fixed.applied)
        assertEquals(null, fixed.lockId)
        assertEquals(false, MainThreadLockWait.queueDetection(fixed))
        assertEquals(true, DetectionPublish.drawWithoutMotion(1, hasGrayAnchor = true, motionQueued = false))
        assertEquals(false, DetectionPublish.drawWithoutMotion(0, hasGrayAnchor = true, motionQueued = false))
        assertEquals(false, DetectionPublish.drawWithoutMotion(1, hasGrayAnchor = true, motionQueued = true))
    }

    @Test fun aRejectedPostDoesNotKeepThePreviousLock() {
        val fixed = MainThreadLockWait.finish(
            posted = false,
            interrupted = false,
            updateRan = false,
            idAfterUpdate = 1,
        )
        assertEquals(false, fixed.applied)
        assertEquals(null, fixed.lockId)
    }

    private data class Published(val lockId: Int?, val trackId: Int, val queuedDraw: Int?, val updateVisible: Boolean)

    /**
     * Two steady frames lock track 1. [holdsBeforeFrame] is how many frames
     * have already aimed at track 2. The frame under test is the next one.
     * AimLock switches on the second of those, while both boxes are still present.
     */
    private fun publish(readBeforeUpdate: Boolean, holdsBeforeFrame: Int = 1): Published {
        val estimator = RelativeDistanceEstimator()
        val start = frame(side(3, 0f), centered(1), side(4, 78f))
        estimator.observe(start)
        estimator.observe(start)
        val aimed = frame(side(3, 0f), aside(1), side(4, 78f), centered(2))
        repeat(holdsBeforeFrame) { estimator.observe(aimed) }
        val posted = ArrayDeque<() -> Unit>()
        var queuedDraw: Int? = null
        val lockId = if (readBeforeUpdate) {
            posted.addLast { estimator.observe(aimed) }
            estimator.selectedTrackId()
        } else {
            OverlayFrameSelection.lockIdForFrame(
                applyLock = { estimator.observe(aimed) },
                readLock = { estimator.selectedTrackId() },
                deferDraw = { id -> queuedDraw = id },
            )
        }
        val track = pumpedTrack(lockId, aimed)
        return Published(
            lockId = lockId,
            trackId = track,
            queuedDraw = queuedDraw,
            updateVisible = estimator.selectedTrackId() == 2,
        )
    }

    private fun pumpedTrack(lockId: Int?, frame: FrameDetections): Int {
        val handoff = TrackingHandoff()
        val gray = GrayFrame(
            4, 4, ByteArray(16) { 40 },
            sequence = frame.sequence,
            capturedAtMs = frame.capturedAtMs,
        )
        handoff.offer(gray)
        val event = handoff.pump(
            nowMs = frame.capturedAtMs + 10,
            detection = DetectionBaseline(frame, gray),
            lockedId = lockId,
        )
        assertNotNull("lock $lockId published no box", event)
        return event!!.frame.boxes.single().trackId
    }

    private fun frame(vararg boxes: BoxObservation) =
        FrameDetections(100, 100, boxes.toList(), sequence = 10, capturedAtMs = 5_000)

    private fun centered(id: Int) =
        BoxObservation("object", "book", 0.9f, 30f, 30f, 70f, 80f, trackId = id)

    private fun aside(id: Int) = centered(id).copy(left = 0f, right = 24f)

    private fun side(id: Int, x: Float) =
        BoxObservation("object", "cup", 0.4f, x, 40f, x + 20f, 80f, trackId = id)
}
