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

/** What one grayscale frame did to the locked boxes. */
data class MotionStep(
    val frame: FrameDetections?,
    val lost: Boolean,
    val reacquired: Boolean,
    val reacquireMs: Int = -1,
)

/**
 * Between MediaPipe results, follows textured patches using bounded block matching.
 * No extra model, cloud service or native tracking library. Estimates TRANSLATION,
 * not depth, identity or camera pose.
 *
 * A failed match hides the box. The last good patch is searched again with a wider
 * window for [reacquireMs], then the lock stays hidden until a new detector result.
 * Coasting or repeating the last rectangle is not used: that is a frozen box.
 */
class MotionTracker(
    private val searchRadius: Int = 16,
    private val patchRadius: Int = 4,
    private val maxPredictionFrames: Int = 45,
    private val reacquireRadius: Int = 28,
    private val reacquireMs: Long = 250L,
) {
    private var previous: GrayFrame? = null
    private var current: FrameDetections? = null
    private var age = 0
    private var lostAtMs: Long? = null
    private var lostGray: GrayFrame? = null
    private var lostFrame: FrameDetections? = null

    fun reset(detections: FrameDetections, capturedAt: GrayFrame) {
        current = detections.copy(boxes = detections.boxes.map { it.copy(points = it.points.toList()) })
        previous = capturedAt
        age = 0
        clearLost()
    }

    fun advance(frame: GrayFrame): FrameDetections? = step(frame, frame.capturedAtMs).frame

    fun step(frame: GrayFrame, nowMs: Long): MotionStep {
        if (lostAtMs != null) return tryReacquire(frame, nowMs)
        val old = previous
        val visible = current
        previous = frame
        if (visible == null || old == null || age++ > maxPredictionFrames) {
            current = null
            return MotionStep(null, lost = visible != null, reacquired = false)
        }
        if (old.width != frame.width || old.height != frame.height) {
            current = null
            return MotionStep(null, lost = true, reacquired = false)
        }
        val shifted = shiftBoxes(old, frame, visible, searchRadius) ?: run {
            // The template is the last good frame, not this blurred one.
            lostAtMs = nowMs
            lostGray = old
            lostFrame = visible
            current = null
            return MotionStep(
                hidden(visible, frame),
                lost = true,
                reacquired = false,
            )
        }
        current = shifted
        return MotionStep(shifted, lost = false, reacquired = false)
    }

    private fun tryReacquire(frame: GrayFrame, nowMs: Long): MotionStep {
        val start = lostAtMs
        val template = lostGray
        val visible = lostFrame
        val elapsed = if (start == null) Long.MAX_VALUE else nowMs - start
        if (start == null || template == null || visible == null || elapsed > reacquireMs ||
            template.width != frame.width || template.height != frame.height
        ) {
            clearLost()
            previous = frame
            current = null
            return MotionStep(null, lost = true, reacquired = false)
        }
        val shifted = shiftBoxes(template, frame, visible, reacquireRadius)
        if (shifted == null) {
            return MotionStep(hidden(visible, frame), lost = true, reacquired = false)
        }
        val found = shifted.copy(sequence = frame.sequence, capturedAtMs = frame.capturedAtMs)
        current = found
        previous = frame
        age = 0
        clearLost()
        return MotionStep(
            found,
            lost = false,
            reacquired = true,
            reacquireMs = elapsed.toInt().coerceAtLeast(0),
        )
    }

    private fun hidden(visible: FrameDetections, frame: GrayFrame) = visible.copy(
        boxes = emptyList(),
        sequence = frame.sequence,
        capturedAtMs = frame.capturedAtMs,
    )

    private fun clearLost() {
        lostAtMs = null
        lostGray = null
        lostFrame = null
    }

    /** Null when every patch is too smooth, out of range, or an unsafe match. */
    private fun shiftBoxes(
        before: GrayFrame,
        after: GrayFrame,
        visible: FrameDetections,
        radius: Int,
    ): FrameDetections? {
        val ratioX = visible.width.toFloat() / after.width
        val ratioY = visible.height.toFloat() / after.height
        val boxes = visible.boxes.mapNotNull { box ->
            val shift = estimate(before, after, box, ratioX, ratioY, radius)
            if (shift == null) {
                // Skin patches are often too smooth for a match. Keep the last
                // hand pose until the landmarker returns instead of dropping it.
                return@mapNotNull if (box.kind == "hand") box else null
            }
            val (dx, dy) = shift
            box.copy(
                left = box.left + dx, right = box.right + dx,
                top = box.top + dy, bottom = box.bottom + dy,
                points = box.points.map { (x, y) -> x + dx to y + dy }
            )
        }
        if (boxes.isEmpty()) return null
        return visible.copy(
            boxes = boxes,
            sequence = after.sequence,
            capturedAtMs = after.capturedAtMs,
        )
    }

    private fun estimate(
        before: GrayFrame,
        after: GrayFrame,
        box: BoxObservation,
        ratioX: Float,
        ratioY: Float,
        radius: Int,
    ): Pair<Float, Float>? {
        val tip = HandTrack.indexTip(box)
        val midX = (if (tip != null) tip.first else (box.left + box.right) / 2f) / ratioX
        val midY = (if (tip != null) tip.second else (box.top + box.bottom) / 2f) / ratioY
        val qX = (box.right - box.left) / 4f / ratioX
        val qY = (box.bottom - box.top) / 4f / ratioY
        val centers = listOf(
            midX.toInt() to midY.toInt(),
            (midX - qX).toInt() to (midY - qY).toInt(),
            (midX + qX).toInt() to (midY + qY).toInt()
        )
        val offsets = centers.mapNotNull { (cx, cy) -> matchPatch(before, after, cx, cy, radius) }
        if (offsets.isEmpty()) return null
        var dx = offsets.map { it.first }.sorted()[offsets.size / 2]
        var dy = offsets.map { it.second }.sorted()[offsets.size / 2]
        val zero = scoreAt(before, after, midX.toInt(), midY.toInt(), 0, 0)
        val best = scoreAt(before, after, midX.toInt(), midY.toInt(), dx, dy)
        if (zero != null && best != null && suppressStationaryJitter(dx, dy, zero, best)) {
            dx = 0
            dy = 0
        }
        return MeasuredSmooth.translation(dx * ratioX, dy * ratioY)
    }

    private fun matchPatch(
        before: GrayFrame,
        after: GrayFrame,
        cx: Int,
        cy: Int,
        radius: Int,
    ): Pair<Int, Int>? {
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
        val stages = arrayOf(4 to radius, 2 to 4, 1 to 1)
        for ((step, window) in stages) {
            val found = searchAround(before, after, cx, cy, bestDx, bestDy, window, step) ?: return null
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

/** A 1px match that is no better than staying put is sensor noise, not motion. */
internal fun suppressStationaryJitter(dx: Int, dy: Int, zeroScore: Int, bestScore: Int): Boolean {
    if (dx == 0 && dy == 0) return false
    if (abs(dx) > 1 || abs(dy) > 1) return false
    return zeroScore <= bestScore + STATIONARY_SLACK
}

private const val STATIONARY_SLACK = 12

/** True when a newly measured frame actually moved or relabeled a box. */
object BoxMotion {
    fun positionsChanged(
        before: List<BoxObservation>,
        after: List<BoxObservation>,
        epsilon: Float = 0.5f,
    ): Boolean {
        // Hiding a lost lock is not a fresh coordinate. Counting it inflates box FPS.
        if (after.isEmpty()) return false
        if (before.size != after.size) return true
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
