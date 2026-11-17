package com.mattumali.visortracker

import android.media.Image
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * Lightweight luminance frame; copied while the MediaCodec Image is valid.
 * Downsampling caps motion analysis to 192 pixels wide even for 1080p video.
 */
data class GrayFrame(
    val width: Int,
    val height: Int,
    val bytes: ByteArray,
    val sequence: Long = 0,
    val capturedAtMs: Long = 0,
) {
    init { require(width > 0 && height > 0 && bytes.size == width * height) }

    fun at(x: Int, y: Int): Int = bytes[y * width + x].toInt() and 255

    companion object {
        fun capture(
            image: Image,
            maxWidth: Int = 192,
            sequence: Long = 0,
            capturedAtMs: Long = 0,
        ): GrayFrame {
            val crop = image.cropRect
            val width = min(maxWidth, crop.width())
            val height = max(1, (crop.height().toDouble() * width / crop.width()).toInt())
            val plane = image.planes[0]
            val buffer = plane.buffer.duplicate()
            val origin = buffer.position()
            val limit = buffer.limit()
            // Sample the luma plane in place. Copying the full frame here allocated
            // about a megabyte on every decoded frame and stalled the decoder.
            val result = ByteArray(width * height)
            val rowStride = plane.rowStride
            val pixelStride = plane.pixelStride
            for (y in 0 until height) {
                val iy = crop.top + y * crop.height() / height
                val row = iy * rowStride - origin
                val out = y * width
                for (x in 0 until width) {
                    val index = row + (crop.left + x * crop.width() / width) * pixelStride
                    val pos = origin + index
                    result[out + x] = if (pos < origin || pos >= limit) 0 else buffer.get(pos)
                }
            }
            return GrayFrame(width, height, result, sequence, capturedAtMs)
        }
    }
}

/**
 * Between MediaPipe results, follows textured patches using bounded block matching.
 * No extra model, cloud service or native tracking library. Estimates TRANSLATION,
 * not depth, identity or camera pose. Resets whenever a new detector result arrives.
 */
class MotionTracker(
    private val searchRadius: Int = 16,
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
        val boxes = visible.boxes.mapNotNull { box ->
            val shift = estimate(old, frame, box, ratioX, ratioY) ?: return@mapNotNull null
            val (dx, dy) = shift
            box.copy(
                left = box.left + dx, right = box.right + dx,
                top = box.top + dy, bottom = box.bottom + dy,
                points = box.points.map { (x, y) -> x + dx to y + dy }
            )
        }
        val published = visible.copy(
            boxes = boxes,
            sequence = frame.sequence,
            capturedAtMs = frame.capturedAtMs,
        )
        // A failed match drops the box. Holding the last coordinate is a frozen box.
        current = if (boxes.isEmpty()) null else published
        return published
    }

    private fun estimate(
        before: GrayFrame,
        after: GrayFrame,
        box: BoxObservation,
        ratioX: Float,
        ratioY: Float
    ): Pair<Float, Float>? {
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
        if (offsets.isEmpty()) return null
        val dx = offsets.map { it.first }.sorted()[offsets.size / 2]
        val dy = offsets.map { it.second }.sorted()[offsets.size / 2]
        return dx * ratioX to dy * ratioY
    }

    private fun matchPatch(before: GrayFrame, after: GrayFrame, cx: Int, cy: Int): Pair<Int, Int>? {
        if (cx - patchRadius < 0 || cy - patchRadius < 0 ||
            cx + patchRadius >= before.width || cy + patchRadius >= before.height) return null

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

        // Coarse step finds head-turn motion; the fine steps restore the old 1 px result.
        var bestDx = 0
        var bestDy = 0
        val stages = arrayOf(4 to searchRadius, 2 to 4, 1 to 1)
        for ((step, radius) in stages) {
            val found = searchAround(before, after, cx, cy, bestDx, bestDy, radius, step) ?: return null
            bestDx = found.first
            bestDy = found.second
        }
        val samples = (patchRadius + 1) * (patchRadius + 1)
        val bestScore = scoreAt(before, after, cx, cy, bestDx, bestDy) ?: return null
        if (bestScore > samples * 38) return null
        return bestDx to bestDy
    }

    private fun searchAround(
        before: GrayFrame,
        after: GrayFrame,
        cx: Int,
        cy: Int,
        originX: Int,
        originY: Int,
        radius: Int,
        step: Int,
    ): Pair<Int, Int>? {
        var bestScore = Int.MAX_VALUE
        var bestDx = originX
        var bestDy = originY
        var found = false
        var dy = -radius
        while (dy <= radius) {
            var dx = -radius
            while (dx <= radius) {
                val tx = originX + dx
                val ty = originY + dy
                val score = scoreAt(before, after, cx, cy, tx, ty)
                if (score != null && (score < bestScore || (score == bestScore &&
                        tx * tx + ty * ty < bestDx * bestDx + bestDy * bestDy))) {
                    bestScore = score
                    bestDx = tx
                    bestDy = ty
                    found = true
                }
                dx += step
            }
            dy += step
        }
        return if (found) bestDx to bestDy else null
    }

    private fun scoreAt(
        before: GrayFrame,
        after: GrayFrame,
        cx: Int,
        cy: Int,
        dx: Int,
        dy: Int,
    ): Int? {
        if (cx + dx - patchRadius < 0 || cy + dy - patchRadius < 0 ||
            cx + dx + patchRadius >= after.width || cy + dy + patchRadius >= after.height) return null
        var score = 0
        for (py in -patchRadius..patchRadius step 2) {
            for (px in -patchRadius..patchRadius step 2) {
                score += abs(before.at(cx + px, cy + py) - after.at(cx + dx + px, cy + dy + py))
            }
        }
        return score
    }
}

/** True when a newly measured frame actually moved or relabeled a box. */
object BoxMotion {
    fun positionsChanged(
        before: List<BoxObservation>,
        after: List<BoxObservation>,
        epsilon: Float = 0.5f,
    ): Boolean {
        if (before.size != after.size) return true
        if (after.isEmpty()) return false
        val prior = HashMap<Int, BoxObservation>(before.size)
        for (box in before) prior[box.trackId] = box
        for (box in after) {
            val old = prior[box.trackId] ?: return true
            if (old.label != box.label || old.kind != box.kind) return true
            if (abs(old.left - box.left) > epsilon) return true
            if (abs(old.top - box.top) > epsilon) return true
            if (abs(old.right - box.right) > epsilon) return true
            if (abs(old.bottom - box.bottom) > epsilon) return true
        }
        return false
    }
}
