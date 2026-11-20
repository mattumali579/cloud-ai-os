package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 10917.mp4 is glasses Live View at 1080x2316. MediaPipe IMAGE mode found a
 * hand in 19 of 170 half-second samples. These frames are the ones that clip
 * actually showed: forearm from the right, phone on the table, pointed object
 * past the fingertip.
 */
class GlassesFootage10917Test {
    private val width = 1080
    private val height = 2316

    private fun barrel() = BoxObservation("object", "cup", 0.86f, 40f, 900f, 280f, 1300f, trackId = 112)
    private fun phone() = BoxObservation("object", "book", 0.9f, 620f, 1550f, 950f, 2100f, trackId = 168)
    private fun arm() = BoxObservation("object", "person", 0.88f, 500f, 900f, 1080f, 2200f, trackId = 140)

    @Test fun aWearerArmIsNotDrawnAsAPerson() {
        val visible = HandPriority.apply(listOf(arm(), barrel(), phone()), width, height)
        assertFalse(visible.any { it.label == "person" })
        assertTrue(visible.any { it.trackId == 112 })
        assertTrue(visible.any { it.trackId == 168 })
    }

    @Test fun missingLandmarksStillPointAlongTheForearmAtTheBarrel() {
        val hand = HandTrack.fromArmBox(arm(), width, height)
        assertEquals(21, hand.points.size)
        assertTrue(IndexPoint.extended(hand, width, height) != null)
        val kept = HandPriority.apply(listOf(arm(), barrel(), phone(), hand), width, height)
        val frame = FrameDetections(width, height, kept)
        val aimed = IndexPoint.aimed(frame)
        assertEquals(112, aimed!!.hit!!.trackId)
        assertFalse(aimed.hit!!.label.equals("person", ignoreCase = true))
        assertFalse(aimed.hit!!.label.equals("book", ignoreCase = true))
    }

    @Test fun thePhoneAtTheScreenCenterDoesNotStealWhileTheArmIsPointing() {
        val hand = HandTrack.fromArmBox(arm(), width, height)
        val kept = HandPriority.apply(listOf(arm(), barrel(), phone(), hand), width, height)
        val frame = FrameDetections(width, height, kept)
        assertEquals(112, IndexPoint.aimed(frame)!!.hit!!.trackId)
        val aim = AimLock()
        aim.observe(frame)
        val locked = aim.observe(frame)
        assertEquals(null, locked.lockedId)
    }

    @Test fun aPersonBoxIsNeverTheLock() {
        assertTrue(!IndexPoint.pointable(arm(), width, height))
        val hand = HandTrack.fromArmBox(arm(), width, height)
        val frame = FrameDetections(width, height, listOf(arm(), hand))
        val aim = AimLock()
        aim.observe(frame)
        val locked = aim.observe(frame)
        assertTrue(locked.lockedId != 140)
    }
}
