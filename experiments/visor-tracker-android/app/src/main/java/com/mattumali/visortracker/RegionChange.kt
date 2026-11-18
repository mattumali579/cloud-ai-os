package com.mattumali.visortracker

import android.graphics.Bitmap
import kotlin.math.max

/**
 * A cheap pixel sample of one locked rectangle.
 * This is not a detector. It only answers whether that rectangle changed enough
 * that the previous box should not be reused. A few levels of noise do not count.
 */
object RegionChange {
    const val GRID = 8
    const val CHANNEL_DELTA = 64
    const val CHANGED_PERCENT = 75

    fun obscured(before: IntArray, after: IntArray): Boolean {
        val count = minOf(before.size, after.size)
        if (count <= 0) return false
        var changed = 0
        for (index in 0 until count) {
            if (channelDelta(before[index], after[index]) > CHANNEL_DELTA) changed++
        }
        return changed * 100 >= count * CHANGED_PERCENT
    }

    fun channelDelta(first: Int, second: Int): Int {
        val red = kotlin.math.abs(((first shr 16) and 255) - ((second shr 16) and 255))
        val green = kotlin.math.abs(((first shr 8) and 255) - ((second shr 8) and 255))
        val blue = kotlin.math.abs((first and 255) - (second and 255))
        return max(red, max(green, blue))
    }

    /** Grid in [bitmap] for a box expressed in the [frameWidth] x [frameHeight] detection space. */
    fun sample(bitmap: Bitmap, box: BoxObservation, frameWidth: Int, frameHeight: Int): IntArray? {
        if (bitmap.isRecycled || frameWidth <= 0 || frameHeight <= 0) return null
        if (bitmap.width <= 1 || bitmap.height <= 1) return null
        if (box.right <= box.left || box.bottom <= box.top) return null
        val scaleX = bitmap.width.toFloat() / frameWidth
        val scaleY = bitmap.height.toFloat() / frameHeight
        val left = box.left * scaleX
        val top = box.top * scaleY
        val right = box.right * scaleX
        val bottom = box.bottom * scaleY
        if (right - left < 2f || bottom - top < 2f) return null
        val grid = GRID
        val out = IntArray(grid * grid)
        for (row in 0 until grid) {
            for (column in 0 until grid) {
                val x = (left + (right - left) * column / (grid - 1)).toInt()
                    .coerceIn(0, bitmap.width - 1)
                val y = (top + (bottom - top) * row / (grid - 1)).toInt()
                    .coerceIn(0, bitmap.height - 1)
                out[row * grid + column] = bitmap.getPixel(x, y)
            }
        }
        return out
    }
}
