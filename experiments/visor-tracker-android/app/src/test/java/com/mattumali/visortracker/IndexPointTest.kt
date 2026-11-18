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
        val fist = pointingHand(90f, 50f).let { hand ->
            val points = hand.points.toMutableList()
            points[IndexPoint.INDEX_TIP] = points[IndexPoint.INDEX_MCP]
            hand.copy(points = points)
        }
        assertNull(IndexPoint.extended(fist, 200, 200))
    }

    @Test fun raySelectsTheObjectItEntersRatherThanTheScreenCenter() {
        val aimed = BoxObservation("object", "cup", 0.9f, 120f, 30f, 170f, 70f, trackId = 2)
        val center = BoxObservation("object", "book", 0.95f, 70f, 70f, 130f, 130f, trackId = 1)
        val frame = FrameDetections(200, 200, listOf(center, aimed, pointingHand(90f, 50f)))
        val ray = IndexPoint.ray(frame)
        val hit = IndexPoint.alongRay(ray!!, frame.boxes, preferId = null)
        assertEquals(2, hit!!.trackId)
    }

    @Test fun replacementPrefersTheHigherScoreThenTheLowerId() {
        val low = BoxObservation("object", "cup", 0.4f, 0f, 0f, 40f, 40f, trackId = 1)
        val high = BoxObservation("object", "book", 0.9f, 50f, 0f, 80f, 20f, trackId = 8)
        val same = BoxObservation("object", "book", 0.9f, 50f, 40f, 90f, 70f, trackId = 3)
        assertEquals(3, IndexPoint.replacement(listOf(low, high, same), excluding = null)!!.trackId)
    }
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
