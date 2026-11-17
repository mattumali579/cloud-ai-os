package com.mattumali.visortracker

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.os.SystemClock
import android.view.View
import java.util.concurrent.atomic.AtomicInteger

/**
 * Transparent, touch-through window over Visor's expanded Live View.
 * Native Live View remains at its own frame rate. Each accepted frame draws the
 * measured box coordinates once. This view does not repaint unchanged boxes.
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
    private var shownSequence = Long.MIN_VALUE
    private val boxUpdates = AtomicInteger(0)
    private val draws = AtomicInteger(0)
    private val distanceEstimator = RelativeDistanceEstimator()
    @Volatile var frameAgeMs: Int = 0
        private set

    private val expire = Runnable {
        latest = null
        frameAgeMs = 0
        invalidate()
    }

    fun consumeBoxUpdates(): Int = boxUpdates.getAndSet(0)
    fun consumeDraws(): Int = draws.getAndSet(0)

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
        if (frame.sequence < shownSequence) return
        shownSequence = frame.sequence
        latest = frame
        receivedAt = SystemClock.elapsedRealtime()
        frameAgeMs = if (frame.capturedAtMs <= 0L) 0 else {
            (receivedAt - frame.capturedAtMs).toInt().coerceAtLeast(0)
        }
        if (frame.boxes.isNotEmpty()) boxUpdates.incrementAndGet()
        removeCallbacks(expire)
        if (frame.boxes.isNotEmpty()) postDelayed(expire, 700L)
        invalidate()
    }

    /** Drops boxes when motion expires, without letting an older clear beat a newer frame. */
    fun clearUpTo(sequence: Long) {
        if (sequence < shownSequence) return
        shownSequence = sequence
        latest = null
        frameAgeMs = 0
        removeCallbacks(expire)
        invalidate()
    }

    fun clear() {
        latest = null
        shownSequence = Long.MIN_VALUE
        frameAgeMs = 0
        removeCallbacks(expire)
        // A restarted camera session must be calibrated again.
        distanceEstimator.clear()
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        draws.incrementAndGet()
        val frame = latest ?: return
        if (width <= 0 || height <= 0 || frame.width <= 0 || frame.height <= 0) return
        for (observation in frame.boxes) {
            val current = CoverGeometry.project(frame.width, frame.height, width, height, observation)
                .let { RectF(it.left, it.top, it.right, it.bottom) }

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
    }

    private fun dp(value: Float) = value * resources.displayMetrics.density
}
