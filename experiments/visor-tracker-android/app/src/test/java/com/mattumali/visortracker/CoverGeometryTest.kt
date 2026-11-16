package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Test

class CoverGeometryTest {
    private fun box(l: Float, t: Float, r: Float, b: Float) =
        BoxObservation("object", "box", 1f, l, t, r, b)

    @Test fun centerRemainsCenteredOnPortraitScreen() {
        val result = CoverGeometry.project(1024, 768, 1080, 1920, box(512f, 384f, 512f, 384f))
        assertEquals(540f, result.left, 0.01f)
        assertEquals(960f, result.top, 0.01f)
    }

    @Test fun portraitFrameFillsPhoneWithTopBottomCrop() {
        val result = CoverGeometry.project(360, 640, 1080, 1920, box(0f, 0f, 360f, 640f))
        assertEquals(0f, result.left, 0.01f)
        assertEquals(0f, result.top, 0.01f)
        assertEquals(1080f, result.right, 0.01f)
        assertEquals(1920f, result.bottom, 0.01f)
    }

    @Test fun wideFrameCropsSymmetricallyOnPortraitScreen() {
        val result = CoverGeometry.project(1024, 768, 1080, 1920, box(0f, 0f, 1024f, 768f))
        assertEquals(-740f, result.left, 0.01f)
        assertEquals(0f, result.top, 0.01f)
        assertEquals(1820f, result.right, 0.01f)
        assertEquals(1920f, result.bottom, 0.01f)
    }
}
