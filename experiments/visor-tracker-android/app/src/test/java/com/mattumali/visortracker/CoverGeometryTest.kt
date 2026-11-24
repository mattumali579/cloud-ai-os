package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CoverGeometryTest {
    private fun box(l: Float, t: Float, r: Float, b: Float) =
        BoxObservation("object", "box", 1f, l, t, r, b)

    @Test fun centerRemainsCenteredOnPortraitScreen() {
        val result = CoverGeometry.project(1024, 768, 1080, 1920, box(512f, 384f, 512f, 384f))
        assertEquals(540f, result.left, 0.01f)
        assertEquals(960f, result.top, 0.01f)
    }

    @Test fun sameAspectPortraitFrameFillsPhone() {
        val result = CoverGeometry.project(360, 640, 1080, 1920, box(0f, 0f, 360f, 640f))
        assertEquals(0f, result.left, 0.01f)
        assertEquals(0f, result.top, 0.01f)
        assertEquals(1080f, result.right, 0.01f)
        assertEquals(1920f, result.bottom, 0.01f)
    }

    @Test fun wideFrameLetterboxesSymmetricallyOnPortraitScreen() {
        val result = CoverGeometry.project(1024, 768, 1080, 1920, box(0f, 0f, 1024f, 768f))
        assertEquals(0f, result.left, 0.01f)
        assertEquals(555f, result.top, 0.01f)
        assertEquals(1080f, result.right, 0.01f)
        assertEquals(1365f, result.bottom, 0.01f)
    }

    @Test fun centeredObjectLandsOnThePortraitScreen() {
        val cup = box(412f, 284f, 612f, 484f)
        val projected = CoverGeometry.project(1024, 768, 1080, 1920, cup)
        val centerX = (projected.left + projected.right) / 2f
        val centerY = (projected.top + projected.bottom) / 2f
        assertEquals(540f, centerX, 0.01f)
        assertEquals(960f, centerY, 0.01f)
        assertTrue(CoverGeometry.isVisible(1024, 768, 1080, 1920, cup))
    }

    @Test fun fullCameraRasterRemainsVisibleInsideLetterbox() {
        val leftEdge = box(0f, 300f, 40f, 468f)
        assertTrue(CoverGeometry.isVisible(1024, 768, 1080, 1920, leftEdge))
        assertFalse(CoverGeometry.isVisible(1024, 768, 1080, 1920, box(512f, 384f, 512f, 384f)))
        assertFalse(CoverGeometry.isVisible(0, 768, 1080, 1920, box(10f, 10f, 40f, 40f)))
    }

    @Test fun recordingGeometryMapsCameraExactlyBetweenBlackBands() {
        val topLeft = CoverGeometry.projectPoint(1080, 1440, 1080, 2316, 0f, 0f)
        val bottomRight = CoverGeometry.projectPoint(1080, 1440, 1080, 2316, 1080f, 1440f)
        assertEquals(0f, topLeft.first, 0.01f)
        assertEquals(438f, topLeft.second, 0.01f)
        assertEquals(1080f, bottomRight.first, 0.01f)
        assertEquals(1878f, bottomRight.second, 0.01f)
    }
}
