package com.mattumali.visortracker

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import java.util.concurrent.atomic.AtomicInteger

/**
 * Transparent, touch-through window over Visor's expanded Live View.
 * Native Live View remains at its own frame rate. Each accepted frame draws the
 * measured box coordinates once. This view does not repaint unchanged boxes.
 */
class TrackingOverlayView(context: Context) : View(context) {
    init {
        // A View with no background can skip onDraw. The proof mark has to draw
        // before any detection exists.
        setWillNotDraw(false)
    }
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
    private val note = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.WHITE
        textSize = dp(11f)
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
    private var probeLines: List<String> = emptyList()
    private var picking = false
    var onPicked: ((String) -> Unit)? = null
    @Volatile var frameAgeMs: Int = 0
        private set

    private val expire = Runnable {
        latest = null
        frameAgeMs = 0
        invalidate()
    }

    fun consumeBoxUpdates(): Int = boxUpdates.getAndSet(0)
    fun consumeDraws(): Int = draws.getAndSet(0)

    /** Corner text that stays visible when there are no boxes. Empty hides it. */
    fun setPicking(enabled: Boolean) {
        picking = enabled
        isClickable = enabled
        invalidate()
    }

    fun setProbe(lines: List<String>) {
        if (lines == probeLines) return
        probeLines = lines
        invalidate()
    }

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

    fun selectedTrackId(): Int? = distanceEstimator.selectedTrackId()

    fun notificationDistance(): String = distanceEstimator.shortStatus(latest?.boxes.orEmpty())

    fun updateFrame(frame: FrameDetections) {
        if (frame.sequence < shownSequence) return
        val previous = latest
        shownSequence = frame.sequence
        latest = frame
        receivedAt = SystemClock.elapsedRealtime()
        frameAgeMs = if (frame.capturedAtMs <= 0L) 0 else {
            (receivedAt - frame.capturedAtMs).toInt().coerceAtLeast(0)
        }
        val previousBoxes = previous?.boxes.orEmpty()
        val moved = BoxMotion.positionsChanged(previousBoxes, frame.boxes)
        val cleared = frame.boxes.isEmpty() && previousBoxes.isNotEmpty()
        if (moved) boxUpdates.incrementAndGet()
        removeCallbacks(expire)
        if (frame.boxes.isNotEmpty()) postDelayed(expire, 700L)
        if (moved || cleared || previous == null) invalidate()
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
        drawProbe(canvas)
        if (picking) canvas.drawText("TAP THE OBJECT TO LOCK", dp(12f), dp(28f), text)
        val frame = latest ?: return
        canvas.drawText(NearbyCandidates.CAPTION, dp(12f), dp(18f), text)
        canvas.drawText(distanceEstimator.banner(frame.boxes), dp(12f), dp(34f), text)
        canvas.drawText(RelativeDistanceEstimator.LIMITATION, dp(12f), dp(48f), note)
        if (frame.boxes.isEmpty()) return
        if (width <= 0 || height <= 0 || frame.width <= 0 || frame.height <= 0) return
        for (observation in frame.boxes) {
            val current = CoverGeometry.project(frame.width, frame.height, width, height, observation)
                .let { RectF(it.left, it.top, it.right, it.bottom) }

            val color = when (observation.kind) {
                "hand" -> 0xFFFFB300.toInt()
                "face" -> 0xFF00D9FF.toInt()
                else -> 0xFF80F45D.toInt()
            }
            val locked = observation.trackId == distanceEstimator.selectedTrackId()
            stroke.color = if (locked) Color.WHITE else color
            stroke.strokeWidth = if (locked) dp(5f) else dp(2.5f)
            canvas.drawRoundRect(current, dp(7f), dp(7f), stroke)
            val feet = distanceEstimator.estimate(frame.height, observation, frame.sequence)
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

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (!picking) return false
        if (event.actionMasked == MotionEvent.ACTION_UP) onPicked?.invoke(lockAt(event.x, event.y))
        return true
    }

    private fun lockAt(x: Float, y: Float): String {
        val frame = latest ?: return "No camera frame yet"
        if (frame.width <= 0 || frame.height <= 0 || width <= 0 || height <= 0) {
            return "Overlay has no size yet"
        }
        var chosen: BoxObservation? = null
        var best = Float.MAX_VALUE
        for (box in frame.boxes) {
            if (box.kind != "object" || box.trackId <= 0) continue
            val projected = CoverGeometry.project(frame.width, frame.height, width, height, box)
            val cx = (projected.left + projected.right) / 2f
            val cy = (projected.top + projected.bottom) / 2f
            val inside = x in projected.left..projected.right && y in projected.top..projected.bottom
            val distance = kotlin.math.hypot(x - cx, y - cy)
            if ((inside || distance <= dp(48f)) && distance < best) {
                chosen = box
                best = distance
            }
        }
        val hit = chosen ?: return "Missed. Tap the box, not the empty picture."
        return distanceEstimator.select(hit.trackId) + " " + hit.label
    }

    private fun drawProbe(canvas: Canvas) {
        val lines = probeLines
        if (lines.isEmpty() || width <= 0) return
        val left = dp(12f)
        val top = dp(52f)
        val lineH = dp(16f)
        val pad = dp(8f)
        var maxW = dp(120f)
        for (line in lines) maxW = maxOf(maxW, text.measureText(line))
        val right = (left + maxW + pad * 2).coerceAtMost(width - dp(12f))
        val bottom = top + pad * 2 + lineH * lines.size
        val box = RectF(left, top, right, bottom)
        canvas.drawRoundRect(box, dp(6f), dp(6f), background)
        stroke.color = 0xFF80F45D.toInt()
        canvas.drawRoundRect(box, dp(6f), dp(6f), stroke)
        lines.forEachIndexed { index, line ->
            canvas.drawText(line, left + pad, top + pad + lineH * (index + 1) - dp(3f), text)
        }
    }

    private fun dp(value: Float) = value * resources.displayMetrics.density
}
