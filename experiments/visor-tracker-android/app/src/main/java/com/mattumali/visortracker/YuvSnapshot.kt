package com.mattumali.visortracker

import android.graphics.Bitmap
import android.media.Image

/**
 * Copy the bytes MediaPipe needs BEFORE returning from LiveVideoDecoder.
 * Android invalidates the Image after the callback. The decoder thread only
 * bulk-copies the three planes (reused buffers). The per-pixel RGB loop runs
 * later, on the inference worker, and only for the downscaled output.
 */
class YuvSnapshot private constructor(
    private val frameWidth: Int,
    private val frameHeight: Int,
    private val y: ByteArray,
    private val yLength: Int,
    private val yRowStride: Int,
    private val yPixelStride: Int,
    private val yOffsetX: Int,
    private val yOffsetY: Int,
    private val u: ByteArray,
    private val uLength: Int,
    private val uRowStride: Int,
    private val uPixelStride: Int,
    private val uOffsetX: Int,
    private val uOffsetY: Int,
    private val v: ByteArray,
    private val vLength: Int,
    private val vRowStride: Int,
    private val vPixelStride: Int,
    private val vOffsetX: Int,
    private val vOffsetY: Int,
) {
    private var planesReleased = false

    fun toBitmap(maxWidth: Int = 384): Bitmap {
        check(!planesReleased) { "YUV planes were released before conversion" }
        val (width, height) = outputSize(frameWidth, frameHeight, maxWidth)
        val rgb = argbScratch(width * height)
        writeArgb(
            frameWidth, frameHeight, maxWidth,
            y, yLength, yRowStride, yPixelStride, yOffsetX, yOffsetY,
            u, uLength, uRowStride, uPixelStride, uOffsetX, uOffsetY,
            v, vLength, vRowStride, vPixelStride, vOffsetX, vOffsetY,
            rgb,
        )
        return sharedBitmap(rgb, width, height)
    }

    /** Return plane buffers to the pool. Safe to call more than once. */
    fun releasePlanes() {
        if (planesReleased) return
        planesReleased = true
        PlanePool.give(y)
        PlanePool.give(u)
        PlanePool.give(v)
    }

    companion object {
        private val bitmapLock = Any()
        private var sharedRgb: IntArray = IntArray(0)
        private var sharedBitmap: Bitmap? = null

        internal fun retainedForTest(frameWidth: Int, frameHeight: Int): YuvSnapshot {
            val y = ByteArray(frameWidth * frameHeight) { 16.toByte() }
            val uv = ByteArray((frameWidth / 2) * (frameHeight / 2)) { 128.toByte() }
            return YuvSnapshot(
                frameWidth, frameHeight,
                y, y.size, frameWidth, 1, 0, 0,
                uv, uv.size, frameWidth / 2, 1, 0, 0,
                uv, uv.size, frameWidth / 2, 1, 0, 0,
            )
        }

        fun capture(image: Image): YuvSnapshot {
            require(image.format == android.graphics.ImageFormat.YUV_420_888) {
                "Expected hardware-decoded YUV_420_888; got format " + image.format
            }
            val crop = image.cropRect
            val planes = image.planes
            val yPlane = copyPlane(planes[0], 1, crop.left, crop.top)
            val uPlane = copyPlane(planes[1], 2, crop.left, crop.top)
            val vPlane = copyPlane(planes[2], 2, crop.left, crop.top)
            return YuvSnapshot(
                crop.width(), crop.height(),
                yPlane.data, yPlane.length, yPlane.rowStride, yPlane.pixelStride, yPlane.offsetX, yPlane.offsetY,
                uPlane.data, uPlane.length, uPlane.rowStride, uPlane.pixelStride, uPlane.offsetX, uPlane.offsetY,
                vPlane.data, vPlane.length, vPlane.rowStride, vPlane.pixelStride, vPlane.offsetX, vPlane.offsetY,
            )
        }

        fun outputSize(frameWidth: Int, frameHeight: Int, maxWidth: Int): Pair<Int, Int> {
            require(frameWidth > 0 && frameHeight > 0) {
                "YUV frame is ${frameWidth}x${frameHeight}"
            }
            require(maxWidth > 0) { "maxWidth must be positive" }
            val width = frameWidth.coerceAtMost(maxWidth)
            val height = ((width.toDouble() / frameWidth) * frameHeight).toInt()
            require(height > 0) { "YUV frame ${frameWidth}x${frameHeight} scaled to an empty image" }
            return width to height
        }

        /**
         * Downscale limited-range YUV into [rgb]. Touches one source sample per
         * output pixel, not a full-resolution intermediate bitmap.
         * Returns the output width and height written at the front of [rgb].
         */
        fun writeArgb(
            frameWidth: Int,
            frameHeight: Int,
            maxWidth: Int,
            y: ByteArray,
            yLength: Int,
            yRowStride: Int,
            yPixelStride: Int,
            yOffsetX: Int,
            yOffsetY: Int,
            u: ByteArray,
            uLength: Int,
            uRowStride: Int,
            uPixelStride: Int,
            uOffsetX: Int,
            uOffsetY: Int,
            v: ByteArray,
            vLength: Int,
            vRowStride: Int,
            vPixelStride: Int,
            vOffsetX: Int,
            vOffsetY: Int,
            rgb: IntArray,
        ): Pair<Int, Int> {
            require(
                yRowStride > 0 && yPixelStride > 0 &&
                    uRowStride > 0 && uPixelStride > 0 &&
                    vRowStride > 0 && vPixelStride > 0
            ) { "YUV stride must be positive" }
            require(yLength > 0 && uLength > 0 && vLength > 0) { "YUV plane is empty" }
            val (width, height) = outputSize(frameWidth, frameHeight, maxWidth)
            require(rgb.size.toLong() >= width.toLong() * height) {
                "RGB buffer ${rgb.size} is smaller than ${width}x${height}"
            }
            for (row in 0 until height) {
                val srcY = (row.toLong() * frameHeight / height).toInt()
                val yBase = (srcY.toLong() + yOffsetY) * yRowStride
                val uBase = (srcY.toLong() / 2 + uOffsetY) * uRowStride
                val vBase = (srcY.toLong() / 2 + vOffsetY) * vRowStride
                val rowOut = row * width
                for (column in 0 until width) {
                    val srcX = (column.toLong() * frameWidth / width).toInt()
                    val yIndex = planeIndex(yBase, srcX.toLong() + yOffsetX, yPixelStride, yLength, "Y")
                    val uIndex = planeIndex(uBase, srcX.toLong() / 2 + uOffsetX, uPixelStride, uLength, "U")
                    val vIndex = planeIndex(vBase, srcX.toLong() / 2 + vOffsetX, vPixelStride, vLength, "V")
                    val yy = y[yIndex].toInt() and 255
                    val uu = u[uIndex].toInt() and 255
                    val vv = v[vIndex].toInt() and 255
                    rgb[rowOut + column] = yuvToArgb(yy, uu, vv)
                }
            }
            return width to height
        }

        /**
         * Index of one sample in a plane copied from that plane's buffer position.
         * A miss is a broken frame, not a black pixel: painting black here made the
         * detector report zero objects with no conversion fault.
         */
        fun planeIndex(rowBase: Long, sample: Long, pixelStride: Int, length: Int, plane: String): Int {
            val index = rowBase + sample * pixelStride
            if (index < 0L || index >= length) {
                throw IllegalArgumentException(
                    "$plane sample $index is outside plane length $length"
                )
            }
            return index.toInt()
        }

        fun yuvToArgb(y: Int, u: Int, v: Int): Int {
            val c = (y - 16).coerceAtLeast(0)
            val d = u - 128
            val e = v - 128
            val r = ((298 * c + 409 * e + 128) shr 8).coerceIn(0, 255)
            val g = ((298 * c - 100 * d - 208 * e + 128) shr 8).coerceIn(0, 255)
            val b = ((298 * c + 516 * d + 128) shr 8).coerceIn(0, 255)
            return (255 shl 24) or (r shl 16) or (g shl 8) or b
        }

        /** The worker reuses one bitmap. Recycling it would blank the next frame. */
        fun recycleDetached(bitmap: Bitmap?) {
            if (bitmap == null) return
            synchronized(bitmapLock) {
                if (bitmap === sharedBitmap) return
            }
            if (!bitmap.isRecycled) bitmap.recycle()
        }

        private fun argbScratch(pixels: Int): IntArray {
            synchronized(bitmapLock) {
                if (sharedRgb.size < pixels) sharedRgb = IntArray(pixels)
                return sharedRgb
            }
        }

        private fun sharedBitmap(rgb: IntArray, width: Int, height: Int): Bitmap {
            synchronized(bitmapLock) {
                val existing = sharedBitmap
                if (existing != null && !existing.isRecycled &&
                    existing.width == width && existing.height == height
                ) {
                    existing.setPixels(rgb, 0, width, 0, 0, width, height)
                    return existing
                }
                val created = Bitmap.createBitmap(rgb, width, height, Bitmap.Config.ARGB_8888)
                sharedBitmap = created
                return created
            }
        }

        private fun copyPlane(plane: Image.Plane, subsampling: Int, cropLeft: Int, cropTop: Int): HeldPlane {
            val buffer = plane.buffer.duplicate()
            val length = buffer.remaining()
            require(length > 0) { "YUV plane is empty" }
            val data = PlanePool.take(length)
            buffer.get(data, 0, length)
            return HeldPlane(
                data, length, plane.rowStride, plane.pixelStride,
                cropLeft / subsampling, cropTop / subsampling,
            )
        }
    }
}

private class HeldPlane(
    val data: ByteArray,
    val length: Int,
    val rowStride: Int,
    val pixelStride: Int,
    val offsetX: Int,
    val offsetY: Int,
)

internal object PlanePool {
    private val free = ArrayList<ByteArray>()

    fun take(size: Int): ByteArray {
        synchronized(free) {
            val index = free.indexOfFirst { it.size >= size }
            if (index >= 0) return free.removeAt(index)
        }
        return ByteArray(size)
    }

    fun give(array: ByteArray) {
        if (array.size > 4_000_000) return
        synchronized(free) {
            if (free.size < 8) free.add(array)
        }
    }
}
