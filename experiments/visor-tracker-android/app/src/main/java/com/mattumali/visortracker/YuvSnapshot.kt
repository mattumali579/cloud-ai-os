package com.mattumali.visortracker

import android.graphics.Bitmap
import android.media.Image

/**
 * Copy MediaCodec's YUV_420_888 output BEFORE returning from LiveVideoDecoder's
 * callback. Android invalidates its Image after the callback. Only captures a frame
 * when the inference worker is available; never keeps an Image or codec buffer alive.
 */
class YuvSnapshot private constructor(
    private val frameWidth: Int,
    private val frameHeight: Int,
    private val y: Plane,
    private val u: Plane,
    private val v: Plane,
) {
    private class Plane(
        val data: ByteArray,
        val rowStride: Int,
        val pixelStride: Int,
    ) {
        fun get(x: Int, y: Int): Int {
            val index = y * rowStride + x * pixelStride
            return if (index >= 0 && index < data.size) data[index].toInt() and 255 else 128
        }
    }

    fun toBitmap(maxWidth: Int = 384): Bitmap {
        val width = frameWidth.coerceAtMost(maxWidth)
        val height = ((width.toDouble() / frameWidth) * frameHeight).toInt().coerceAtLeast(1)
        val rgb = IntArray(width * height)
        for (row in 0 until height) {
            val srcY = (row.toLong() * frameHeight / height).toInt()
            for (column in 0 until width) {
                val srcX = (column.toLong() * frameWidth / width).toInt()
                val yy = y.get(srcX, srcY)
                val uu = u.get(srcX / 2, srcY / 2)
                val vv = v.get(srcX / 2, srcY / 2)
                rgb[row * width + column] = yuvToArgb(yy, uu, vv)
            }
        }
        return Bitmap.createBitmap(rgb, width, height, Bitmap.Config.ARGB_8888)
    }

    companion object {
        fun capture(image: Image): YuvSnapshot {
            require(image.format == android.graphics.ImageFormat.YUV_420_888) {
                "Expected hardware-decoded YUV_420_888; got format " + image.format
            }
            val crop = image.cropRect
            require(crop.left == 0 && crop.top == 0) {
                "Unexpected nonzero video crop " + crop.toShortString()
            }
            val planes = image.planes.map { plane ->
                val buffer = plane.buffer.duplicate()
                val data = ByteArray(buffer.remaining())
                buffer.get(data)
                Plane(data, plane.rowStride, plane.pixelStride)
            }
            return YuvSnapshot(crop.width(), crop.height(), planes[0], planes[1], planes[2])
        }

        // BT.601 limited-range YUV, with integer math and channel saturation.
        fun yuvToArgb(y: Int, u: Int, v: Int): Int {
            val c = (y - 16).coerceAtLeast(0)
            val d = u - 128
            val e = v - 128
            val r = ((298 * c + 409 * e + 128) shr 8).coerceIn(0, 255)
            val g = ((298 * c - 100 * d - 208 * e + 128) shr 8).coerceIn(0, 255)
            val b = ((298 * c + 516 * d + 128) shr 8).coerceIn(0, 255)
            return (255 shl 24) or (r shl 16) or (g shl 8) or b
        }
    }
}
