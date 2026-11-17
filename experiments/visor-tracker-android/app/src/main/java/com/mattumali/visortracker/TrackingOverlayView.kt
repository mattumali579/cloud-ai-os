package com.mattumali.visortracker

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.os.SystemClock
import android.view.View
import kotlin.math.abs

/**
 * Transparent, touch-through window over Visor's expanded Live View.
 * Native Live View remains at its own frame rate. Detector results are
 * updated by the existing VDK pipeline; this view only draws boxes/labels.
 */
class TrackingOverlayView(context: Context) : View(context) {
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(2.5f)
    }
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.WHITE
        textSize = dp(13f)
        isFakeBoldText = true
    }
    private val background = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = 0xCC141414.toInt()
    }
    private val point = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private var latest: FrameDetections? = null
    private var receivedAt = 0L
    private val smoothBoxes = mutableMapOf<Int, RectF>()
    private val distanceEstimator = RelativeDistanceEstimator()

    /** Calibrate only the physical object currently nearest the camera center. */
    fun calibrateDistance(feet: Float): String {
        val frame = latest ?: return "No detected objects yet. Start Live View and tracking."
        if (SystemClock.elapsedRealtime() - receivedAt > 1300L) {
            return "No fresh video frame. Open Live View and retry."
        }
        val result = distanceEstimator.calibrate(frame, feet)
        invalidate()
        return result
    }

    fun clearDistance() {
        distanceEstimator.clear()
        invalidate()
    }

    fun updateFrame(frame: FrameDetections) {
        latest = frame
        receivedAt = SystemClock.elapsedRealtime()
        val ids = frame.boxes.map { it.trackId }.toSet()
        smoothBoxes.keys.retainAll(ids)
        invalidate()
    }

    fun clear() {
        latest = null
        smoothBoxes.clear()
        // A restarted camera session must be calibrated again.
        distanceEstimator.clear()
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val frame = latest ?: return
        if (width <= 0 || height <= 0 || frame.width <= 0 || frame.height <= 0) return
        // Fail closed: don't leave stale boxes floating over other apps.
        if (SystemClock.elapsedRealtime() - receivedAt > 1300L) {
            clear()
            return
        }
        for (observation in frame.boxes) {
            val desired = CoverGeometry.project(frame.width, frame.height, width, height, observation)
            val current = smoothBoxes.getOrPut(observation.trackId) {
                RectF(desired.left, desired.top, desired.right, desired.bottom)
            }
            // Visual interpolation reduces jumps; it does NOT create new detections.
            current.left += (desired.left - current.left) * 0.40f
            current.top += (desired.top - current.top) * 0.40f
            current.right += (desired.right - current.right) * 0.40f
            current.bottom += (desired.bottom - current.bottom) * 0.40f

            val color = when (observation.kind) {
                "hand" -> 0xFFFFB300.toInt()
                "face" -> 0xFF00D9FF.toInt()
                else -> 0xFF80F45D.toInt()
            }
            stroke.color = color
            canvas.drawRoundRect(current, dp(7f), dp(7f), stroke)
            val feet = distanceEstimator.estimate(frame.height, observation)
            val distanceLabel = if (feet == null) "" else " ~%.1f ft".format(
                java.util.Locale.US, feet
            )
            val label = observation.label + " #" + observation.trackId + distanceLabel
            val labelW = text.measureText(label) + dp(12f)
            val labelTop = (current.top - dp(25f)).coerceAtLeast(0f)
            canvas.drawRoundRect(
                current.left, labelTop, current.left + labelW,
                labelTop + dp(23f), dp(4f), dp(4f), background
            )
            canvas.drawText(label, current.left + dp(6f), labelTop + dp(16f), text)
            if (observation.kind == "hand") {
                point.color = color
                val scale = kotlin.math.max(width.toFloat() / frame.width, height.toFloat() / frame.height)
                val ox = (width - frame.width * scale) / 2f
                val oy = (height - frame.height * scale) / 2f
                observation.points.forEach { (x, y) ->
                    canvas.drawCircle(ox + x * scale, oy + y * scale, dp(2.5f), point)
                }
            }
        }
        // Smooth drawing on the display even while the detector processes its next frame.
        postInvalidateDelayed(16L)
    }

    private fun dp(value: Float) = value * resources.displayMetrics.density
}
