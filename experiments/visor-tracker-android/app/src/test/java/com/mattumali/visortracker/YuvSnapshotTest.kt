package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
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

    @Test fun paddedCropReadsThePixelInsideTheCrop() {
        val rowStride = 8
        val y = ByteArray(rowStride * 4) { 16.toByte() }
        y[2 * rowStride + 2] = 235.toByte()
        val uv = ByteArray(rowStride * 2) { 128.toByte() }
        val rgb = IntArray(4 * 2)
        val (width, height) = YuvSnapshot.writeArgb(
            4, 2, 8,
            y, y.size, rowStride, 1, 2, 2,
            uv, uv.size, rowStride, 1, 1, 1,
            uv, uv.size, rowStride, 1, 1, 1,
            rgb,
        )
        assertEquals(4, width)
        assertEquals(2, height)
        assertEquals(0xffffffff.toInt(), rgb[0])
        assertEquals(0xff000000.toInt(), rgb[1])
    }

    @Test fun semiPlanarChromaKeepsLimitedRangeRed() {
        val y = ByteArray(4 * 2) { 81.toByte() }
        val u = ByteArray(8) { index -> if (index % 2 == 0) 90.toByte() else 16.toByte() }
        val v = ByteArray(8) { index -> if (index % 2 == 0) 240.toByte() else 16.toByte() }
        val rgb = IntArray(4 * 2)
        YuvSnapshot.writeArgb(
            4, 2, 4,
            y, y.size, 4, 1, 0, 0,
            u, u.size, 4, 2, 0, 0,
            v, v.size, 4, 2, 0, 0,
            rgb,
        )
        assertEquals(0xffff0000.toInt(), rgb[0])
        assertEquals(0xffff0000.toInt(), rgb[5])
    }

    @Test fun emptyOrZeroSizedFramesFailInsteadOfBecomingBlack() {
        assertThrows(IllegalArgumentException::class.java) {
            YuvSnapshot.outputSize(0, 720, 384)
        }
        assertThrows(IllegalArgumentException::class.java) {
            YuvSnapshot.outputSize(1280, -1, 384)
        }
        val y = ByteArray(8) { 235.toByte() }
        val uv = ByteArray(4) { 128.toByte() }
        val rgb = IntArray(4)
        assertThrows(IllegalArgumentException::class.java) {
            YuvSnapshot.writeArgb(
                2, 2, 2,
                y, 0, 2, 1, 0, 0,
                uv, uv.size, 2, 1, 0, 0,
                uv, uv.size, 2, 1, 0, 0,
                rgb,
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            YuvSnapshot.writeArgb(
                2, 2, 2,
                y, y.size, 0, 1, 0, 0,
                uv, uv.size, 2, 1, 0, 0,
                uv, uv.size, 2, 1, 0, 0,
                rgb,
            )
        }
    }

    @Test fun aShortLumaPlaneOrOverflowingStrideIsNotPaintedBlack() {
        val y = ByteArray(4) { 235.toByte() }
        val uv = ByteArray(4) { 128.toByte() }
        val rgb = IntArray(8)
        assertThrows(IllegalArgumentException::class.java) {
            YuvSnapshot.writeArgb(
                4, 2, 4,
                y, y.size, 4, 1, 0, 0,
                uv, uv.size, 2, 1, 0, 0,
                uv, uv.size, 2, 1, 0, 0,
                rgb,
            )
        }
        val wide = ByteArray(8) { 235.toByte() }
        assertThrows(IllegalArgumentException::class.java) {
            YuvSnapshot.writeArgb(
                2, 2, 2,
                wide, wide.size, 2_000_000_000, 1, 0, 0,
                uv, uv.size, 2, 1, 0, 0,
                uv, uv.size, 2, 1, 0, 0,
                IntArray(4),
            )
        }
    }

    @Test fun releasedPlanesCannotBeConverted() {
        val snapshot = YuvSnapshot.retainedForTest(4, 2)
        snapshot.releasePlanes()
        assertThrows(IllegalStateException::class.java) {
            snapshot.toBitmap()
        }
    }

    @Test fun planePoolDoesNotCallJdk21AddLast() {
        val bytes = PlanePool::class.java.getResourceAsStream("PlanePool.class")!!.readBytes()
        assertFalse(String(bytes, Charsets.ISO_8859_1).contains("addLast"))
        val buffer = PlanePool.take(32123)
        buffer[0] = 9
        PlanePool.give(buffer)
        assertTrue(PlanePool.take(32123) === buffer)
    }
}
