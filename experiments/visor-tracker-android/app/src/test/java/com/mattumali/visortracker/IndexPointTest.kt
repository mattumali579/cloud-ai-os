package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class IndexPointTest {
    @Test fun extendedIndexBuildsARayThroughTheFingertip() {
        val hand = pointingHand(tipX = 90f, tipY = 50f)
        val ray = IndexPoint.extended(hand, 200, 200)
        assertTrue(ray != null)
        assertEquals(5, ray!!.joints.size)
        assertEquals(90f, ray.tipX, 0.1f)
        assertEquals(50f, ray.tipY, 0.1f)
        assertTrue(ray.dirX > 0.9f)
        assertEquals(IndexPoint.WRIST, 0)
        assertEquals(hand.points[IndexPoint.INDEX_TIP], ray.joints[4])
    }

    @Test fun openPalmAndFistAreNotPointing() {
        val open = pointingHand(90f, 50f).let { hand ->
            val points = hand.points.toMutableList()
            points[12] = 100f to 50f
            hand.copy(points = points)
        }
        assertNull(IndexPoint.extended(open, 200, 200))
        assertNull(IndexPoint.extended(closedFist(), 200, 200))
    }

    @Test fun raySelectsTheObjectItEntersRatherThanTheScreenCenter() {
        val aimed = BoxObservation("object", "cup", 0.9f, 120f, 30f, 170f, 70f, trackId = 2)
        val center = BoxObservation("object", "book", 0.95f, 70f, 70f, 130f, 130f, trackId = 1)
        val frame = FrameDetections(200, 200, listOf(center, aimed, pointingHand(90f, 50f)))
        val ray = IndexPoint.ray(frame)
        val hit = IndexPoint.alongRay(ray!!, frame.boxes, preferId = null)
        assertEquals(2, hit!!.trackId)
    }

    @Test fun aHeldHandStillBuildsARaySoPointingSurvivesAMiss() {
        val live = pointingHand(90f, 50f)
        val held = live.copy(held = true)
        val frame = FrameDetections(200, 200, listOf(held))
        val ray = IndexPoint.ray(frame)
        assertTrue(ray != null)
        assertTrue(kotlin.math.abs(ray!!.dirZ) >= 0f)
        assertTrue(ray.reach > 200f)
    }

    @Test fun depthAngledFingerUsesTheFingertipAsAStableCursor() {
        val hand = depthPointingHand(tipX = 100f, tipY = 80f)
        val ray = IndexPoint.extended(hand, 200, 200)
        assertTrue(ray != null)
        assertTrue(ray!!.depthDominant)
        assertTrue(kotlin.math.abs(ray.dirZ) > 0.4f)

        val underTip = BoxObservation("object", "cup", 0.85f, 90f, 70f, 120f, 110f, trackId = 4)
        val elsewhere = BoxObservation("object", "book", 0.95f, 140f, 20f, 190f, 60f, trackId = 2)
        assertEquals(4, IndexPoint.alongRay(ray, listOf(elsewhere, underTip), preferId = null)!!.trackId)
    }

    @Test fun depthCursorDoesNotSelectAnUnrelatedDistantBox() {
        val ray = IndexPoint.extended(depthPointingHand(100f, 80f), 200, 200)!!
        val distant = BoxObservation("object", "book", 0.95f, 160f, 150f, 195f, 195f, trackId = 2)
        assertNull(IndexPoint.alongRay(ray, listOf(distant), preferId = null))
    }

    @Test fun aSlightlyUncurledMiddleFingerStillCountsAsIndexPointing() {
        val hand = pointingHand(90f, 50f).let { original ->
            val points = original.points.toMutableList()
            points[12] = 82f to 50f
            original.copy(points = points)
        }
        val ray = IndexPoint.extended(hand, 200, 200)
        assertTrue(ray != null)
        assertEquals(90f, ray!!.tipX, 0.1f)
    }

    @Test fun collapsedIndexDoesNotAimFromTheMiddleFinger() {
        val hand = pointingHand(90f, 50f).let { original ->
            val points = original.points.toMutableList()
            points[IndexPoint.INDEX_TIP] = points[IndexPoint.INDEX_MCP]
            points[IndexPoint.INDEX_DIP] = points[IndexPoint.INDEX_PIP]
            points[12] = 90f to 50f
            points[11] = 70f to 50f
            points[10] = 50f to 50f
            original.copy(points = points)
        }
        assertNull(IndexPoint.extended(hand, 200, 200))
    }

    @Test fun aBoxContainingTheFingertipIsNotTheAimTarget() {
        val sofa = BoxObservation("object", "couch", 0.9f, 40f, 20f, 190f, 160f, trackId = 144)
        val bottle = BoxObservation("object", "bottle", 0.8f, 120f, 30f, 170f, 70f, trackId = 7)
        val frame = FrameDetections(200, 200, listOf(sofa, bottle, pointingHand(90f, 50f)))
        val ray = IndexPoint.ray(frame)!!
        val hit = IndexPoint.alongRay(
            ray, frame.boxes, preferId = null,
            frameWidth = frame.width, frameHeight = frame.height,
        )
        assertEquals(7, hit!!.trackId)
    }

    @Test fun aScreenFillingPersonBoxIsNotTheAimTarget() {
        val giant = BoxObservation("object", "person", 0.99f, 0f, 0f, 200f, 200f, trackId = 346)
        val cup = BoxObservation("object", "cup", 0.8f, 120f, 30f, 170f, 70f, trackId = 766)
        val frame = FrameDetections(200, 200, listOf(giant, cup, pointingHand(90f, 50f)))
        val ray = IndexPoint.ray(frame)!!
        val hit = IndexPoint.alongRay(
            ray, frame.boxes, preferId = null,
            frameWidth = frame.width, frameHeight = frame.height,
        )
        assertEquals(766, hit!!.trackId)
    }

    @Test fun aSideOnCollapsedIndexAimsAlongTheForearmNotTheKnuckles() {
        val hand = pointingHand(90f, 50f).let { original ->
            val points = original.points.toMutableList()
            val mcp = points[IndexPoint.INDEX_MCP]
            points[IndexPoint.INDEX_DIP] = points[IndexPoint.INDEX_PIP]
            points[IndexPoint.INDEX_TIP] = mcp
            points[12] = mcp.first + 2f to mcp.second + 3f
            points[16] = mcp.first - 1f to mcp.second + 3f
            points[20] = mcp.first - 2f to mcp.second + 2f
            original.copy(points = points)
        }
        val ray = IndexPoint.extended(hand, 200, 200)
        assertTrue(ray != null)
        assertTrue(ray!!.dirX > 0.8f)
        assertTrue(ray.tipX > hand.points[IndexPoint.INDEX_MCP].first)
    }

    @Test fun aimedReturnsTheHitWithoutASecondPass() {
        val cup = BoxObservation("object", "cup", 0.9f, 120f, 30f, 170f, 70f, trackId = 2)
        val frame = FrameDetections(200, 200, listOf(cup, pointingHand(90f, 50f)))
        val aimed = IndexPoint.aimed(frame)
        assertEquals(2, aimed!!.hit!!.trackId)
        assertTrue(aimed.ray.dirX > 0.5f)
    }

    @Test fun anInvertedSideOnRayFlipsToHitTheObject() {
        val cup = BoxObservation("object", "cup", 0.9f, 10f, 40f, 40f, 70f, trackId = 7)
        val hand = pointingHand(90f, 50f).let { original ->
            val points = original.points.toMutableList()
            val mcp = points[IndexPoint.INDEX_MCP]
            points[IndexPoint.INDEX_DIP] = points[IndexPoint.INDEX_PIP]
            points[IndexPoint.INDEX_TIP] = mcp
            points[12] = mcp.first + 2f to mcp.second + 3f
            points[16] = mcp.first - 1f to mcp.second + 3f
            points[20] = mcp.first - 2f to mcp.second + 2f
            original.copy(points = points)
        }
        val raw = IndexPoint.extended(hand, 200, 200)!!
        assertTrue(raw.reversible)
        val oriented = IndexPoint.oriented(raw, listOf(cup), 200, 200)
        assertTrue(oriented.dirX < 0f)
        assertEquals(7, IndexPoint.alongRay(oriented, listOf(cup), null, frameWidth = 200, frameHeight = 200)!!.trackId)
    }

    @Test fun aPersonBoxIsNeverAPointableTarget() {
        val person = BoxObservation("object", "person", 0.99f, 80f, 20f, 160f, 180f, trackId = 140)
        val cup = BoxObservation("object", "cup", 0.8f, 120f, 30f, 170f, 70f, trackId = 7)
        assertTrue(!IndexPoint.pointable(person, 200, 200))
        val frame = FrameDetections(200, 200, listOf(person, cup, pointingHand(90f, 50f)))
        val hit = IndexPoint.alongRay(IndexPoint.ray(frame)!!, frame.boxes, null, frameWidth = 200, frameHeight = 200)
        assertEquals(7, hit!!.trackId)
    }

    @Test fun replacementPrefersTheHigherScoreThenTheLowerId() {
        val low = BoxObservation("object", "cup", 0.4f, 0f, 0f, 40f, 40f, trackId = 1)
        val high = BoxObservation("object", "book", 0.9f, 50f, 0f, 80f, 20f, trackId = 8)
        val same = BoxObservation("object", "book", 0.9f, 50f, 40f, 90f, 70f, trackId = 3)
        assertEquals(3, IndexPoint.replacement(listOf(low, high, same), excluding = null)!!.trackId)
    }
}

internal fun closedFist(): BoxObservation {
    val points = MutableList(21) { 80f to 100f }
    points[IndexPoint.WRIST] = 70f to 100f
    points[IndexPoint.INDEX_MCP] = 90f to 100f
    points[IndexPoint.INDEX_PIP] = 84f to 108f
    points[IndexPoint.INDEX_DIP] = 80f to 106f
    points[IndexPoint.INDEX_TIP] = 78f to 104f
    points[12] = 86f to 110f
    points[16] = 74f to 110f
    points[20] = 68f to 108f
    return BoxObservation(
        "hand", "hand", 0.92f,
        60f, 90f, 100f, 120f,
        points = points, trackId = 51,
    )
}

internal fun pointingHand(tipX: Float, tipY: Float): BoxObservation {
    val points = MutableList(21) { 0f to tipY }
    points[IndexPoint.WRIST] = tipX - 80f to tipY
    points[IndexPoint.INDEX_MCP] = tipX - 60f to tipY
    points[IndexPoint.INDEX_PIP] = tipX - 40f to tipY
    points[IndexPoint.INDEX_DIP] = tipX - 20f to tipY
    points[IndexPoint.INDEX_TIP] = tipX to tipY
    points[9] = tipX - 58f to tipY + 14f
    points[10] = tipX - 52f to tipY + 20f
    points[11] = tipX - 50f to tipY + 18f
    points[12] = tipX - 54f to tipY + 16f
    points[13] = tipX - 64f to tipY + 18f
    points[14] = tipX - 60f to tipY + 24f
    points[15] = tipX - 58f to tipY + 22f
    points[16] = tipX - 62f to tipY + 20f
    points[17] = tipX - 70f to tipY + 22f
    points[18] = tipX - 68f to tipY + 28f
    points[19] = tipX - 66f to tipY + 26f
    points[20] = tipX - 70f to tipY + 24f
    points[1] = tipX - 74f to tipY - 8f
    points[2] = tipX - 68f to tipY - 4f
    points[3] = tipX - 64f to tipY
    points[4] = tipX - 66f to tipY + 4f
    return BoxObservation(
        "hand", "hand", 0.92f,
        tipX - 80f, tipY - 10f, tipX, tipY + 30f,
        points = points, trackId = 50,
    )
}

internal fun depthPointingHand(tipX: Float, tipY: Float): BoxObservation {
    val hand = pointingHand(tipX, tipY)
    val points = hand.points.toMutableList()
    // Foreshorten the projected index while keeping a straight 3D finger.
    points[IndexPoint.WRIST] = tipX - 24f to tipY
    points[IndexPoint.INDEX_MCP] = tipX - 18f to tipY
    points[IndexPoint.INDEX_PIP] = tipX - 12f to tipY
    points[IndexPoint.INDEX_DIP] = tipX - 4f to tipY
    points[IndexPoint.INDEX_TIP] = tipX to tipY
    points[12] = tipX - 22f to tipY + 6f
    points[16] = tipX - 25f to tipY + 8f
    points[20] = tipX - 28f to tipY + 7f
    val depths = MutableList(21) { 0f }
    depths[IndexPoint.WRIST] = 50f
    depths[IndexPoint.INDEX_MCP] = 35f
    depths[IndexPoint.INDEX_PIP] = 20f
    depths[IndexPoint.INDEX_DIP] = 8f
    depths[IndexPoint.INDEX_TIP] = -8f
    depths[12] = 46f
    depths[16] = 47f
    depths[20] = 48f
    return hand.copy(
        left = tipX - 32f,
        right = tipX + 8f,
        points = points,
        depths = depths,
    )
}
