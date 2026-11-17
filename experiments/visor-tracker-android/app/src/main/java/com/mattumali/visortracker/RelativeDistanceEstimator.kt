package com.mattumali.visortracker

import java.util.Locale
import kotlin.math.abs

/**
 * An offline, calibrated *relative* distance estimate for ONE physical target.
 *
 * Calibration records how tall that same target appeared at a user-measured
 * distance. Later frames use apparent-height ratios (similar triangles).
 * No fixed object-size assumptions, network calls, or extra ML model.
 *
 * NOT a depth sensor: rotating, tilting, occluding, or relabeling an object
 * can change the bounding box and invalidate this rough estimate.
 *
 * The lock is the track id. Reordering boxes or a temporary category flicker
 * does not move the calibration onto another item. If that id leaves the
 * frame, readiness is [DistanceReadiness.STALE] and no other box inherits
 * the feet value.
 */
enum class DistanceReadiness { UNCALIBRATED, LIVE, STALE }

/**
 * Similar-triangles scale for one object: D = D0 × (h0 / h).
 * Heights may be pixels or fractions of the frame; they must use the same unit.
 * This is not a depth measurement. [percentError] is for tests and logs.
 */
object DistanceScale {
    fun feet(referenceFeet: Float, referenceHeight: Float, observedHeight: Float): Float? {
        if (!referenceFeet.isFinite() || !referenceHeight.isFinite() || !observedHeight.isFinite()) return null
        if (referenceHeight <= 0f || observedHeight <= 0f) return null
        val raw = referenceFeet * referenceHeight / observedHeight
        if (!raw.isFinite() || raw !in 0.5f..150f) return null
        return raw
    }

    fun percentError(estimated: Float, measuredTruth: Float): Float {
        if (!measuredTruth.isFinite() || measuredTruth == 0f) return Float.POSITIVE_INFINITY
        return abs(estimated - measuredTruth) / abs(measuredTruth) * 100f
    }
}

class RelativeDistanceEstimator {
    @Volatile private var selectedId: Int? = null
    private var originalHeightFraction = 0f
    private var originalFeet = 0f
    private var smoothedFeet: Float? = null

    fun calibrate(frame: FrameDetections, measuredFeet: Float): String {
        if (!measuredFeet.isFinite() || measuredFeet !in 0.5f..150f) {
            return "Enter a real measured distance between 0.5 and 150 ft"
        }
        if (frame.width <= 0 || frame.height <= 0) return "No camera image for calibration"

        // A touch-through overlay cannot receive taps. Aim the glasses at the
        // target and choose the object whose box is closest to camera center.
        val objects = frame.boxes.filter {
            it.kind == "object" && it.trackId > 0 &&
                it.right > it.left && it.bottom > it.top &&
                it.score >= 0.3f
        }
        val centered = objects.minByOrNull {
            val cx = (it.left + it.right) * 0.5f / frame.width
            val cy = (it.top + it.bottom) * 0.5f / frame.height
            (cx - 0.5f) * (cx - 0.5f) + (cy - 0.5f) * (cy - 0.5f)
        } ?: return "No detected object. Face the object and keep Live View running."

        val centerX = (centered.left + centered.right) * 0.5f / frame.width
        val centerY = (centered.top + centered.bottom) * 0.5f / frame.height
        if (abs(centerX - 0.5f) > 0.3f || abs(centerY - 0.5f) > 0.3f) {
            return "Aim the center of your glasses at the object, then calibrate"
        }
        val heightFraction = (centered.bottom - centered.top) / frame.height
        if (heightFraction !in 0.08f..0.98f) {
            return "Target too small or cropped. Move so the full object is visible."
        }

        selectedId = centered.trackId
        originalHeightFraction = heightFraction
        originalFeet = measuredFeet
        smoothedFeet = measuredFeet
        return "Distance locked: ${centered.label} #${centered.trackId} at %.1f ft. Same object and orientation only.".format(
            Locale.US, measuredFeet
        )
    }

    /** Returns null for all other objects, invalid boxes, or uncalibrated state. */
    fun estimate(frameHeight: Int, box: BoxObservation): Float? {
        if (box.kind != "object" || box.trackId != selectedId || frameHeight <= 0) return null
        val fraction = (box.bottom - box.top) / frameHeight
        if (!fraction.isFinite() || fraction < 0.03f) return null
        val raw = DistanceScale.feet(originalFeet, originalHeightFraction, fraction) ?: return null

        // Small box jitter is smoothed. A real size change (more than 8%) shows
        // on the next frame so the feet reading is not stuck on the reference.
        val previous = smoothedFeet
        val next = if (previous == null || previous <= 0f ||
            abs(raw - previous) / previous >= 0.08f
        ) raw else previous + (raw - previous) * 0.35f
        smoothedFeet = next.coerceIn(0.5f, 150f)
        return smoothedFeet
    }

    /** Short line for the ongoing notification. Blank before the first lock. */
    fun shortStatus(boxes: List<BoxObservation>): String = when (readiness(boxes)) {
        DistanceReadiness.UNCALIBRATED -> ""
        DistanceReadiness.STALE -> "distance unavailable"
        DistanceReadiness.LIVE -> "cal %.1f ft".format(Locale.US, originalFeet)
    }

    /** Overlay line while Live View is up. */
    fun banner(boxes: List<BoxObservation>): String = when (readiness(boxes)) {
        DistanceReadiness.UNCALIBRATED -> "Aim at one full object, then Calibrate"
        DistanceReadiness.STALE -> "distance unavailable"
        DistanceReadiness.LIVE ->
            "Calibrated %.1f ft · same object, same orientation".format(Locale.US, originalFeet)
    }

    fun selectedTrackId(): Int? = selectedId

    fun readiness(boxes: List<BoxObservation>): DistanceReadiness {
        val id = selectedId ?: return DistanceReadiness.UNCALIBRATED
        return if (boxes.any { it.kind == "object" && it.trackId == id }) {
            DistanceReadiness.LIVE
        } else {
            DistanceReadiness.STALE
        }
    }

    companion object {
        const val LIMITATION =
            "No depth sensor. Tilt, rotation, or occlusion can miss by more than 25%."
    }

    fun clear() {
        selectedId = null
        originalHeightFraction = 0f
        originalFeet = 0f
        smoothedFeet = null
    }
}
