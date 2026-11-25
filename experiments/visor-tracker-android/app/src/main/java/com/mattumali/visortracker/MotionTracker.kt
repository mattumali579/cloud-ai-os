package com.mattumali.visortracker

import android.media.Image
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * Lightweight luminance frame; copied while the MediaCodec Image is valid.
 * Downsampling caps motion analysis to 192 pixels wide even for 1080p video.
 */
data class GrayFrame(val width: Int, val height: Int, val bytes: ByteArray) {
    init { require(width > 0 && height > 0 && bytes.size == width * height) }

    fun at(x: Int, y: Int): Int = bytes[y * width + x].toInt() and 255

    companion object {
        fun capture(image: Image, maxWidth: Int = 192): GrayFrame {
            val crop = image.cropRect
            val width = min(maxWidth, crop.width())
            val height = max(1, (crop.height().toDouble() * width / crop.width()).toInt())
            val plane = image.planes[0]
            val buffer = plane.buffer.duplicate()
            val base = buffer.position()
            val result = ByteArray(width * height)
            for (y in 0 until height) {
                val iy = crop.top + y * crop.height() / height
                for (x in 0 until width) {
                    val ix = crop.left + x * crop.width() / width
                    result[y * width + x] = buffer.get(
                        base + iy * plane.rowStride + ix * plane.pixelStride
                    )
                }
            }
            return GrayFrame(width, height, result)
        }
    }
}

/**
 * Between MediaPipe results, follows textured patches using bounded block matching.
 * No extra model, cloud service or native tracking library. Estimates TRANSLATION,
 * not depth, identity or camera pose. Resets whenever a new detector result arrives.
 */
class MotionTracker(
    private val searchRadius: Int = 6,
    private val patchRadius: Int = 4,
    private val maxPredictionFrames: Int = 36
) {
    private var previous: GrayFrame? = null
    private var current: FrameDetections? = null
    private var age = 0

    fun reset(detections: FrameDetections, capturedAt: GrayFrame) {
        current = detections.copy(boxes = detections.boxes.map { it.copy(points = it.points.toList()) })
        previous = capturedAt
        age = 0
    }

    fun advance(frame: GrayFrame): FrameDetections? {
        val old = previous
        val visible = current
        previous = frame
        if (visible == null || old == null || age++ > maxPredictionFrames) {
            current = null
            return null
        }
        if (old.width != frame.width || old.height != frame.height) {
            current = null
            return null
        }
        val ratioX = visible.width.toFloat() / frame.width
        val ratioY = visible.height.toFloat() / frame.height
        val boxes = visible.boxes.map { box ->
            val (dx, dy) = estimate(old, frame, box, ratioX, ratioY)
            box.copy(
                left = box.left + dx, right = box.right + dx,
                top = box.top + dy, bottom = box.bottom + dy,
                points = box.points.map { (x, y) -> x + dx to y + dy }
            )
        }
        return visible.copy(boxes = boxes).also { current = it }
    }

    private fun estimate(
        before: GrayFrame,
        after: GrayFrame,
        box: BoxObservation,
        ratioX: Float,
        ratioY: Float
    ): Pair<Float, Float> {
        val midX = (box.left + box.right) / 2f / ratioX
        val midY = (box.top + box.bottom) / 2f / ratioY
        val qX = (box.right - box.left) / 4f / ratioX
        val qY = (box.bottom - box.top) / 4f / ratioY
        val centers = listOf(
            midX.toInt() to midY.toInt(),
            (midX - qX).toInt() to (midY - qY).toInt(),
            (midX + qX).toInt() to (midY + qY).toInt()
        )
        val offsets = centers.mapNotNull { (cx, cy) -> matchPatch(before, after, cx, cy) }
        if (offsets.isEmpty()) return 0f to 0f
        val dx = offsets.map { it.first }.sorted()[offsets.size / 2]
        val dy = offsets.map { it.second }.sorted()[offsets.size / 2]
        return dx * ratioX to dy * ratioY
    }

    private fun matchPatch(before: GrayFrame, after: GrayFrame, cx: Int, cy: Int): Pair<Int, Int>? {
        val safety = searchRadius + patchRadius + 1
        if (cx < safety || cy < safety ||
            cx >= before.width - safety || cy >= before.height - safety) return null

        // Skip smooth, texture-free areas where many movements look equally likely.
        var minimum = 255
        var maximum = 0
        for (dy in -patchRadius..patchRadius step 2) {
            for (dx in -patchRadius..patchRadius step 2) {
                val pixel = before.at(cx + dx, cy + dy)
                minimum = min(minimum, pixel)
                maximum = max(maximum, pixel)
            }
        }
        if (maximum - minimum < 16) return null

        var bestScore = Int.MAX_VALUE
        var bestDx = 0
        var bestDy = 0
        for (dy in -searchRadius..searchRadius) {
            for (dx in -searchRadius..searchRadius) {
                var score = 0
                for (py in -patchRadius..patchRadius step 2) {
                    for (px in -patchRadius..patchRadius step 2) {
                        score += abs(before.at(cx + px, cy + py) -
                            after.at(cx + dx + px, cy + dy + py))
                    }
                }
                // Prefer zero movement if multiple candidates tie.
                if (score < bestScore || (score == bestScore &&
                        dx * dx + dy * dy < bestDx * bestDx + bestDy * bestDy)) {
                    bestScore = score
                    bestDx = dx
                    bestDy = dy
                }
            }
        }
        val samples = (patchRadius + 1) * (patchRadius + 1)
        if (bestScore > samples * 38) return null
        return bestDx to bestDy
    }
}
