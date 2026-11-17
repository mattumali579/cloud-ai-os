package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class YuvSnapshotTest {
    @Test fun limitedRangeBlackIsBlack() {
        assertEquals(0xff000000.toInt(), YuvSnapshot.yuvToArgb(16, 128, 128))
    }

    @Test fun limitedRangeWhiteIsWhite() {
        assertEquals(0xffffffff.toInt(), YuvSnapshot.yuvToArgb(235, 128, 128))
    }

    @Test fun redChannelIsClamped() {
        val pixel = YuvSnapshot.yuvToArgb(81, 90, 240)
        val r = (pixel shr 16) and 255
        val g = (pixel shr 8) and 255
        val b = pixel and 255
        assertEquals(true, r > g && r > b)
    }

    @Test fun downscaleWritesOnlyTheOutputPixels() {
        val frameWidth = 16
        val frameHeight = 8
        val y = ByteArray(frameWidth * frameHeight) { 235.toByte() }
        val uv = ByteArray((frameWidth / 2) * (frameHeight / 2)) { 128.toByte() }
        val rgb = IntArray(8 * 4)
        val (width, height) = YuvSnapshot.writeArgb(
            frameWidth, frameHeight, 8,
            y, y.size, frameWidth, 1, 0, 0,
            uv, uv.size, frameWidth / 2, 1, 0, 0,
            uv, uv.size, frameWidth / 2, 1, 0, 0,
            rgb,
        )
        assertEquals(8, width)
        assertEquals(4, height)
        assertTrue(width * height < frameWidth * frameHeight)
        assertEquals(0xffffffff.toInt(), rgb[width * (height / 2) + width / 2])
    }
}
