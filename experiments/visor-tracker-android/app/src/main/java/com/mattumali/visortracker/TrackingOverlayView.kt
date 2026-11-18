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
    private val nearby = NearbyCandidates()
    private var probeLines: List<String> = emptyList()
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

    fun drawnBoxCount(): Int {
        val frame = latest ?: return 0
        val boxes = frame.boxes
        if (boxes.isEmpty()) return 0
        // Before the window is measured, keep the stored count. Once the view has
        // a size, a box that cover-crops entirely off screen is not drawn.
        if (width <= 0 || height <= 0 || frame.width <= 0 || frame.height <= 0) return boxes.size
        return boxes.count { CoverGeometry.isVisible(frame.width, frame.height, width, height, it) }
    }

    /**
     * Sees every detector object, including ones that are not the drawn box,
     * so a held point at a new center can switch the lock.
     */
    fun observeCandidates(frame: FrameDetections): String? {
        val note = distanceEstimator.observe(frame)
        if (note != null) invalidate()
        return note
    }

    fun updateFrame(frame: FrameDetections): String? {
        val previous = latest
        val lockedId = distanceEstimator.selectedTrackId()
        val incoming = if (frame.boxes.isEmpty()) {
            emptyList()
        } else {
            nearby.select(frame.width, frame.height, frame.boxes, lockedId)
        }
        val now = SystemClock.elapsedRealtime()
        val accepted = OverlayDiagnostics.acceptFrame(
            frameSequence = frame.sequence,
            shownSequence = shownSequence,
            incomingBoxes = incoming.size,
            showingTrackId = previous?.boxes?.firstOrNull()?.trackId,
            incomingTrackId = incoming.firstOrNull()?.trackId,
            lockedId = lockedId,
            captureIsRecent = OverlayDiagnostics.captureIsRecent(now, frame.capturedAtMs, BoxHold.HOLD_MS),
        )
        if (!accepted) return null
        shownSequence = OverlayDiagnostics.nextShownSequence(shownSequence, frame.sequence, accepted = true)
        val ageMs = if (receivedAt == 0L) Long.MAX_VALUE else now - receivedAt
        val kept = BoxHold.keep(previous?.boxes.orEmpty(), incoming, ageMs)
        val display = OverlayDiagnostics.frameToDraw(frame, previous, incoming.size, kept)
        latest = display
        if (incoming.isNotEmpty()) receivedAt = now
        frameAgeMs = if (display.boxes.isEmpty()) 0 else OverlayDiagnostics.ageMs(now, display.capturedAtMs)
        val previousBoxes = previous?.boxes.orEmpty()
        val moved = BoxMotion.positionsChanged(previousBoxes, display.boxes)
        val cleared = display.boxes.isEmpty() && previousBoxes.isNotEmpty()
        if (moved) boxUpdates.incrementAndGet()
        removeCallbacks(expire)
        if (display.boxes.isNotEmpty()) postDelayed(expire, BoxHold.HOLD_MS)
        if (moved || cleared || previous == null || kept.isNotEmpty()) invalidate()
        return null
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

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        if (w > 0 && h > 0 && latest?.boxes?.isNotEmpty() == true) invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        draws.incrementAndGet()
        drawProbe(canvas)
        val frame = latest ?: return
        canvas.drawText(NearbyCandidates.CAPTION, dp(12f), dp(18f), text)
        canvas.drawText(distanceEstimator.banner(frame.boxes), dp(12f), dp(34f), text)
        canvas.drawText(RelativeDistanceEstimator.LIMITATION, dp(12f), dp(48f), note)
        if (frame.boxes.isEmpty()) return
        if (width <= 0 || height <= 0 || frame.width <= 0 || frame.height <= 0) {
            // A draw before layout used to return here and never run again.
            if (width <= 0 || height <= 0) {
                post {
                    if (width > 0 && height > 0 && latest?.boxes?.isNotEmpty() == true) invalidate()
                }
            }
            return
        }
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
