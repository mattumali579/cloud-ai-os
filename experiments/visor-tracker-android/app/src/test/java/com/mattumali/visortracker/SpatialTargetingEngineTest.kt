package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SpatialTargetingEngineTest {

    private fun createOpenPalmHand(x: Float, y: Float): BoxObservation {
        val pts = MutableList(21) { x to y }
        pts[IndexPoint.WRIST] = x to y
        // Extend all fingers away from wrist
        pts[IndexPoint.INDEX_MCP] = x + 30f to y - 30f
        pts[IndexPoint.INDEX_PIP] = x + 50f to y - 50f
        pts[IndexPoint.INDEX_DIP] = x + 70f to y - 70f
        pts[IndexPoint.INDEX_TIP] = x + 90f to y - 90f

        pts[9] = x + 35f to y - 25f
        pts[10] = x + 60f to y - 45f
        pts[11] = x + 85f to y - 65f
        pts[12] = x + 110f to y - 85f // Middle extended

        pts[13] = x + 35f to y - 15f
        pts[14] = x + 60f to y - 30f
        pts[15] = x + 85f to y - 45f
        pts[16] = x + 105f to y - 60f // Ring extended

        pts[17] = x + 30f to y - 5f
        pts[18] = x + 50f to y - 10f
        pts[19] = x + 70f to y - 15f
        pts[20] = x + 85f to y - 20f // Pinky extended

        return BoxObservation("hand", "hand", 0.95f, x, y - 100f, x + 120f, y, points = pts, trackId = 1)
    }

    private fun createPhoneGripHand(fw: Int, fh: Int, index: Int): BoxObservation {
        // Wrist at bottom edge of screen (holding phone)
        val wx = fw * 0.5f + (index - 15) * 8f
        val wy = fh - 20f
        val pts = MutableList(21) { wx to wy }
        pts[IndexPoint.WRIST] = wx to wy

        // Curled fingers wrapped around phone
        pts[IndexPoint.INDEX_MCP] = wx - 15f to wy - 40f
        pts[IndexPoint.INDEX_PIP] = wx - 10f to wy - 30f
        pts[IndexPoint.INDEX_DIP] = wx - 5f to wy - 20f
        pts[IndexPoint.INDEX_TIP] = wx to wy - 15f // Curled around bezel

        pts[9] = wx - 25f to wy - 38f
        pts[10] = wx - 20f to wy - 28f
        pts[11] = wx - 15f to wy - 18f
        pts[12] = wx - 10f to wy - 12f

        return BoxObservation(
            "hand", "hand", 0.92f,
            wx - 35f, wy - 50f, wx + 10f, wy,
            points = pts, trackId = 2
        )
    }

    private fun createForeshortenedSideOnPoint(): BoxObservation {
        val pts = MutableList(21) { 125f to 215f }
        val depths = MutableList(21) { 0f }
        pts[IndexPoint.WRIST] = 100f to 250f

        // Thumb is straight and corroborates the side-on hand orientation.
        pts[1] = 105f to 225f
        pts[2] = 120f to 205f
        pts[3] = 135f to 185f
        pts[4] = 150f to 165f

        // Index is straight in 3D but foreshortened/bent-looking in x/y.
        pts[5] = 130f to 210f
        pts[6] = 175f to 190f
        pts[7] = 135f to 170f
        pts[8] = 170f to 145f
        depths[5] = 0f
        depths[6] = 20f
        depths[7] = 35f
        depths[8] = 45f

        // Remaining fingers are curled into the palm.
        pts[9] = 125f to 215f
        pts[10] = 135f to 205f
        pts[11] = 128f to 212f
        pts[12] = 120f to 220f
        pts[13] = 120f to 220f
        pts[14] = 130f to 212f
        pts[16] = 118f to 224f
        pts[17] = 112f to 225f
        pts[18] = 122f to 218f
        pts[20] = 110f to 230f

        return BoxObservation(
            "hand", "hand", 0.90f, 95f, 140f, 180f, 255f,
            points = pts, depths = depths, trackId = 3,
        )
    }

    @Test
    fun negativeTests_openPalmTrialsNeverLock() {
        val engine = SpatialTargetingEngine()
        val targetBox = BoxObservation("object", "cup", 0.9f, 200f, 200f, 300f, 300f, trackId = 10)

        var unintendedLocks = 0
        for (trial in 0 until 30) {
            val hand = createOpenPalmHand(100f + trial * 2f, 150f + trial * 2f)
            val frame = FrameDetections(640, 480, listOf(targetBox, hand))
            val output = engine.step(frame)
            if (output.lockId != null) {
                unintendedLocks++
            }
            assertEquals("Open palm must be classified as OPEN_PALM", SpatialTargetingEngine.HandPose.OPEN_PALM, output.handPose)
        }

        assertEquals("Zero unintended locks required across 30 open-palm trials", 0, unintendedLocks)
    }

    @Test
    fun negativeTests_phoneGripTrialsNeverLock() {
        val engine = SpatialTargetingEngine()
        val targetBox = BoxObservation("object", "cup", 0.9f, 200f, 200f, 300f, 300f, trackId = 10)

        var unintendedLocks = 0
        for (trial in 0 until 30) {
            val hand = createPhoneGripHand(640, 480, trial)
            val frame = FrameDetections(640, 480, listOf(targetBox, hand))
            val output = engine.step(frame)
            if (output.lockId != null) {
                unintendedLocks++
            }
            assertNull("Phone grip must not lock", output.lockId)
        }

        assertEquals("Zero unintended locks required across 30 phone-grip trials", 0, unintendedLocks)
    }

    @Test
    fun sideOnForeshortenedIndexIsAcceptedButThumbsUpIsNot() {
        val sideOn = createForeshortenedSideOnPoint()
        assertEquals(
            SpatialTargetingEngine.HandPose.POINTING_INTENDED,
            SpatialTargetingEngine.classifyPose(sideOn, 640, 480),
        )

        val noDepthCue = sideOn.copy(depths = List(21) { 0f })
        assertTrue(
            "A thumb-straight hand without index depth extension must not use the side-on exception",
            SpatialTargetingEngine.classifyPose(noDepthCue, 640, 480) != SpatialTargetingEngine.HandPose.POINTING_INTENDED,
        )

        val output = SpatialTargetingEngine(directionFramesRequired = 1).step(
            FrameDetections(640, 480, listOf(sideOn)),
        )
        assertEquals("A valid index ray must remain authoritative", 8, output.ray?.sourceLandmark)

        val swapped = sideOn.points.toMutableList().also { points ->
            points[IndexPoint.WRIST] = 200f to 240f
            points[1] = 180f to 220f
            points[2] = 160f to 200f
            points[3] = 140f to 180f
            points[4] = 120f to 160f
            points[5] = 100f to 230f
            points[6] = 95f to 225f
            points[7] = 102f to 222f
            points[8] = 110f to 215f
        }
        val alternate = SpatialTargetingEngine(directionFramesRequired = 1).step(
            FrameDetections(640, 480, listOf(sideOn.copy(points = swapped))),
        )
        assertNull(
            "Thumb landmarks 1..4 must not substitute when the index chain fails; landmark 8 is authoritative",
            alternate.ray,
        )
    }

    @Test
    fun stableHandOwnership_swappingHandIdentityResetsHoverAndLock() {
        val engine = SpatialTargetingEngine(hoverFramesRequired = 2, directionFramesRequired = 1)
        val target = BoxObservation("object", "bottle", 0.9f, 300f, 100f, 350f, 150f, trackId = 10)
        
        // Hand 1 (trackId 1) points at target
        val hand1 = pointingHand(220f, 125f).copy(trackId = 1, score = 0.95f)
        val frame1 = FrameDetections(640, 480, listOf(target, hand1))
        val out1 = engine.step(frame1)
        assertEquals(SpatialTargetingEngine.State.HOVER, out1.state)
        assertEquals(target.trackId, out1.hoverId)

        val out2 = engine.step(frame1)
        assertEquals(SpatialTargetingEngine.State.LOCKED, out2.state)
        assertEquals(target.trackId, out2.lockId)

        // Hand 2 (trackId 2) suddenly replaces Hand 1 with higher score
        val hand2 = pointingHand(220f, 125f).copy(trackId = 2, score = 0.98f)
        val frame2 = FrameDetections(640, 480, listOf(target, hand2))
        val out3 = engine.step(frame2)
        // Hand ownership swap MUST reset hover and lock!
        assertNull("Lock must be reset on hand ownership change", out3.lockId)
        assertTrue(out3.lockChanged)
    }

    @Test
    fun positiveTests_pointingTrialsHitIntendedTarget() {
        var correctTargets = 0
        val totalTrials = 30

        for (trial in 0 until totalTrials) {
            val engine = SpatialTargetingEngine(hoverFramesRequired = 1, directionFramesRequired = 1)
            val targetX = 300f + (trial % 5) * 20f
            val targetY = 100f + (trial / 5) * 30f
            val target = BoxObservation("object", "bottle", 0.9f, targetX, targetY, targetX + 50f, targetY + 50f, trackId = 100 + trial)

            // Aim hand directly at target center
            val hand = pointingHand(targetX - 80f, targetY + 25f)
            val frame = FrameDetections(640, 480, listOf(target, hand))
            val output = engine.step(frame)

            if (output.hitId == target.trackId && output.lockId == target.trackId) {
                correctTargets++
            }
        }

        val accuracy = correctTargets.toFloat() / totalTrials.toFloat()
        assertTrue("Expected >= 90% accuracy on pointing trials, got $accuracy ($correctTargets/$totalTrials)", accuracy >= 0.90f)
    }

    @Test
    fun targetSwitching_deliberateAimSwitchesAfterHysteresis() {
        val engine = SpatialTargetingEngine(hoverFramesRequired = 2, switchFramesRequired = 4, directionFramesRequired = 1)
        val targetA = BoxObservation("object", "cup", 0.9f, 300f, 100f, 360f, 160f, trackId = 10)
        val targetB = BoxObservation("object", "laptop", 0.9f, 300f, 300f, 400f, 400f, trackId = 20)

        // 1. Point at Target A and lock
        val handA = pointingHand(200f, 130f)
        engine.step(FrameDetections(640, 480, listOf(targetA, targetB, handA)))
        val lockA = engine.step(FrameDetections(640, 480, listOf(targetA, targetB, handA)))
        assertEquals("Target A must lock", targetA.trackId, lockA.lockId)

        // 2. Point deliberately at Target B
        val handB = pointingHand(200f, 350f)
        // Frames 1-3 of aiming at B: lock A must still be held (hysteresis)
        for (f in 1..3) {
            val step = engine.step(FrameDetections(640, 480, listOf(targetA, targetB, handB)))
            assertEquals("Lock A must hold during switch hysteresis (frame $f)", targetA.trackId, step.lockId)
        }

        // Frame 4: switch to Target B must complete
        val switchFinal = engine.step(FrameDetections(640, 480, listOf(targetA, targetB, handB)))
        assertEquals("Lock must switch to Target B after 4 frames", targetB.trackId, switchFinal.lockId)
        assertTrue(switchFinal.lockChanged)
    }

    @Test
    fun forwardRayConstraint_neverFlipsBackward() {
        val hand = pointingHand(100f, 100f)
        val ray = SpatialTargetingEngine.constructForwardRay(hand, 640, 480)
        assertNotNull(ray)
        // Ray must point forward (dirX > 0)
        assertTrue("Ray dirX must be positive forward", ray!!.dirX > 0f)

        // Create an inverted hand where tip is behind wrist
        val ptsInverted = hand.points.toMutableList()
        ptsInverted[IndexPoint.WRIST] = 200f to 100f
        ptsInverted[IndexPoint.INDEX_TIP] = 100f to 100f // Tip is to the left of wrist, but finger points left
        ptsInverted[IndexPoint.INDEX_PIP] = 150f to 100f
        val invertedHand = hand.copy(points = ptsInverted)

        // If finger vector points opposite to wrist vector, constructForwardRay must reject it (NO BACKWARD FLIP)
        val invertedPipToTip = ptsInverted[IndexPoint.INDEX_TIP].first - ptsInverted[IndexPoint.INDEX_PIP].first // -50
        val invertedWristToTip = ptsInverted[IndexPoint.INDEX_TIP].first - ptsInverted[IndexPoint.WRIST].first // -100
        // Both point left, dot product is positive (points away from wrist).
        // Now test where finger points back INTO the wrist (pip at 80, tip at 120, wrist at 200):
        ptsInverted[IndexPoint.INDEX_PIP] = 80f to 100f
        ptsInverted[IndexPoint.INDEX_TIP] = 120f to 100f // dir points right towards wrist (from 80 to 120)
        ptsInverted[IndexPoint.WRIST] = 200f to 100f // wrist is at 200
        val backwardFinger = hand.copy(points = ptsInverted)
        val backwardRay = SpatialTargetingEngine.constructForwardRay(backwardFinger, 640, 480)
        assertNull("Ray pointing backward into wrist must be rejected (NO BACKWARD FLIP)", backwardRay)
    }

    @Test
    fun noFingertipInsideBoxAutomaticHit() {
        val hand = pointingHand(250f, 250f)
        // A huge box that contains the fingertip (250, 250)
        val containerBox = BoxObservation("object", "table", 0.8f, 100f, 100f, 500f, 500f, trackId = 99)
        val ray = SpatialTargetingEngine.constructForwardRay(hand, 640, 480)!!

        val hit = SpatialTargetingEngine.findRayIntersection(ray, listOf(containerBox), hand, 640, 480)
        assertNull("Fingertip inside box must not be an automatic hit", hit)
    }

    @Test
    fun noNearestOrCenterFallback() {
        val hand = pointingHand(100f, 100f)
        // Object is behind the hand or completely away from ray
        val offscreenBox = BoxObservation("object", "lamp", 0.9f, 50f, 400f, 120f, 460f, trackId = 88)
        val centerBox = BoxObservation("object", "desk", 0.9f, 250f, 200f, 390f, 280f, trackId = 77)

        val ray = SpatialTargetingEngine.constructForwardRay(hand, 640, 480)!!
        // Hand points along y=100 from x=100 towards x=640 (straight horizontal right).
        // Center box is at y=200..280 (does not intersect horizontal ray at y=100)
        val hit = SpatialTargetingEngine.findRayIntersection(ray, listOf(offscreenBox, centerBox), hand, 640, 480)
        assertNull("Missed ray must NOT fall back to center or nearest object", hit)
    }

    @Test
    fun stateMachineLifecycle() {
        val engine = SpatialTargetingEngine(hoverFramesRequired = 2, lostFramesGraceLimit = 2, directionFramesRequired = 1)
        val target = BoxObservation("object", "mug", 0.9f, 300f, 80f, 360f, 140f, trackId = 42)
        val hand = pointingHand(150f, 110f)

        // 1. NO_HAND
        val out0 = engine.step(FrameDetections(640, 480, listOf(target)))
        assertEquals(SpatialTargetingEngine.State.NO_HAND, out0.state)

        // 2. POINTING -> HOVER (frame 1)
        val out1 = engine.step(FrameDetections(640, 480, listOf(target, hand)))
        assertEquals(SpatialTargetingEngine.State.HOVER, out1.state)
        assertNull(out1.lockId)

        // 3. HOVER -> LOCKED (frame 2)
        val out2 = engine.step(FrameDetections(640, 480, listOf(target, hand)))
        assertEquals(SpatialTargetingEngine.State.LOCKED, out2.state)
        assertEquals(target.trackId, out2.lockId)

        // 4. Lost frame 1 (within grace)
        val out3 = engine.step(FrameDetections(640, 480, listOf(target)))
        assertEquals(SpatialTargetingEngine.State.LOCKED, out3.state)
        assertEquals(target.trackId, out3.lockId)

        // 5. Lost frame 2 (within grace)
        val out4 = engine.step(FrameDetections(640, 480, listOf(target)))
        assertEquals(SpatialTargetingEngine.State.LOCKED, out4.state)
        assertEquals(target.trackId, out4.lockId)

        // 6. Lost frame 3 (exceeds grace limit of 2) -> RELEASED
        val out5 = engine.step(FrameDetections(640, 480, listOf(target)))
        assertEquals(SpatialTargetingEngine.State.RELEASED, out5.state)
        assertNull(out5.lockId)
        assertTrue(out5.isReleased)
    }

    @Test
    fun defaultLockRequiresMoreThanThreeFrames() {
        val engine = SpatialTargetingEngine()
        val target = BoxObservation("object", "laptop", 0.92f, 300f, 80f, 500f, 260f, trackId = 42)
        val hand = pointingHand(150f, 110f)

        repeat(5) { frame ->
            val output = engine.step(FrameDetections(640, 480, listOf(target, hand)))
            assertNull("frame ${frame + 1} must not create an accidental short lock", output.lockId)
        }
        val confirmed = engine.step(FrameDetections(640, 480, listOf(target, hand)))
        assertEquals(target.trackId, confirmed.lockId)
    }

    @Test
    fun armWithoutFreshLandmarksIsNoHand() {
        val engine = SpatialTargetingEngine()
        val arm = BoxObservation("arm", "arm", 0.9f, 400f, 200f, 640f, 480f, trackId = 7)
        val output = engine.step(FrameDetections(640, 480, listOf(arm)))
        assertEquals(SpatialTargetingEngine.State.NO_HAND, output.state)
        assertEquals(SpatialTargetingEngine.HandPose.NONE, output.handPose)
    }

    @Test
    fun oversizedGenericMaskCannotBeatSpecificLaptop() {
        val laptop = BoxObservation("object", "laptop", 0.92f, 250f, 120f, 500f, 360f, trackId = 37)
        val bedding = BoxObservation("object", LabelPolicy.UNCERTAIN, 0.53f, 100f, 60f, 630f, 470f, trackId = 79)
        val filtered = SpatialTargetingEngine.suppressGenericContainers(listOf(bedding, laptop), 640, 480)
        assertFalse(filtered.any { it.trackId == bedding.trackId })
        assertTrue(filtered.any { it.trackId == laptop.trackId })
    }

    @Test
    fun lowConfidenceLocalProposalIsRejectedButStrictGroundedBoxIsEligible() {
        val local = BoxObservation("object", "suitcase", 0.16f, 100f, 100f, 250f, 250f, trackId = 1)
        val grounded = local.copy(label = "laundry basket", score = 0f, trackId = 2, grounded = true)
        assertFalse(SpatialTargetingEngine.isValidTarget(local, 640, 480))
        assertTrue(SpatialTargetingEngine.isValidTarget(grounded, 640, 480))
    }

    @Test
    fun oversizedGroundedStructuralRegionIsRejected() {
        val door = BoxObservation(
            "object", "door", 0f, 170f, 0f, 380f, 430f,
            trackId = 8, grounded = true,
        )
        assertFalse(SpatialTargetingEngine.isValidTarget(door, 640, 480))
    }

    @Test
    fun onlyGroundedBoxMayAcquireWhenFingertipIsInside() {
        val ray = SpatialTargetingEngine.SpatialRay(
            tipX = 320f, tipY = 200f, dirX = -1f, dirY = 0f, dirZ = 0f,
            reach = 1000f, joints = emptyList(),
        )
        val local = BoxObservation("object", "suitcase", 0.9f, 280f, 160f, 380f, 260f, trackId = 1)
        val grounded = local.copy(label = "air conditioner", score = 0f, trackId = 2, grounded = true)
        val hand = pointingHand(320f, 200f)
        assertNull(SpatialTargetingEngine.findRayIntersection(ray, listOf(local), hand, 640, 480))
        assertEquals(grounded.trackId, SpatialTargetingEngine.findRayIntersection(ray, listOf(grounded), hand, 640, 480)?.trackId)
    }

    @Test
    fun groundedBoxEdgeGrazeDoesNotAcquire() {
        val ray = SpatialTargetingEngine.SpatialRay(
            tipX = 320f, tipY = 200f, dirX = 0f, dirY = -1f, dirZ = 0f,
            reach = 1000f, joints = emptyList(),
        )
        val edgeOnly = BoxObservation(
            "object", "door", 0f, 250f, 100f, 390f, 201f,
            trackId = 9, grounded = true,
        )
        assertNull(
            "A fingertip within one pixel of a grounded box edge is localization jitter, not a hit",
            SpatialTargetingEngine.findRayIntersection(ray, listOf(edgeOnly), pointingHand(320f, 200f), 640, 480),
        )
    }

    @Test
    fun groundedBoxUsesBoundedRayCorridor() {
        val ray = SpatialTargetingEngine.SpatialRay(
            tipX = 300f, tipY = 300f, dirX = 0f, dirY = -1f, dirZ = 0f,
            reach = 1000f, joints = emptyList(),
        )
        val grounded = BoxObservation(
            "object", "bottle", 0f, 345f, 80f, 380f, 180f,
            trackId = 12, grounded = true,
        )
        assertEquals(
            grounded.trackId,
            SpatialTargetingEngine.findRayIntersection(
                ray, listOf(grounded), pointingHand(300f, 300f), 1080, 1440,
            )?.trackId,
        )
    }

    @Test
    fun pointingIntoEmptySpaceReleasesStaleLockAfterGrace() {
        val engine = SpatialTargetingEngine(
            hoverFramesRequired = 1,
            directionFramesRequired = 1,
            lostFramesGraceLimit = 2,
        )
        val target = BoxObservation("object", "cup", 0.9f, 300f, 80f, 360f, 140f, trackId = 42)
        val atTarget = pointingHand(150f, 110f)
        val locked = engine.step(FrameDetections(640, 480, listOf(target, atTarget)))
        assertEquals(target.trackId, locked.lockId)

        val emptySpace = pointingHand(150f, 350f)
        repeat(2) {
            val held = engine.step(FrameDetections(640, 480, listOf(target, emptySpace)))
            assertEquals(target.trackId, held.lockId)
        }
        val released = engine.step(FrameDetections(640, 480, listOf(target, emptySpace)))
        assertEquals(SpatialTargetingEngine.State.RELEASED, released.state)
        assertNull(released.lockId)
    }
}
