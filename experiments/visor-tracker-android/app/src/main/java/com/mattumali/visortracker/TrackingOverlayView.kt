package com.mattumali.visortracker

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
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
    private val handLine = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(2f)
        strokeCap = Paint.Cap.ROUND
    }
    private val handOutline = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(3.5f)
        strokeJoin = Paint.Join.ROUND
        strokeCap = Paint.Cap.ROUND
        color = 0xFFFFB300.toInt()
    }
    private val indexFill = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = 0xFF00E5FF.toInt()
    }
    private val indexRing = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(2.5f)
        color = Color.WHITE
    }
    private val handPath = Path()
    private var latest: FrameDetections? = null
    private var receivedAt = 0L
    private var shownSequence = Long.MIN_VALUE
    private val boxUpdates = AtomicInteger(0)
    private val draws = AtomicInteger(0)
    private val distanceEstimator = RelativeDistanceEstimator()
    var strict3DMode: Boolean = true
        private set
    private var spatialEngine = SpatialTargetingEngine(strict3DMode = true)

    fun setStrict3DMode(enabled: Boolean) {
        if (strict3DMode != enabled) {
            strict3DMode = enabled
            spatialEngine = SpatialTargetingEngine(strict3DMode = enabled)
            spatialSequence = Long.MIN_VALUE
            spatialOutput = null
            postInvalidate()
        }
    }
    private var spatialSequence = Long.MIN_VALUE
    private var spatialOutput: SpatialTargetingEngine.Output? = null
    private var spatialFrameWidth = 0
    private var spatialFrameHeight = 0
    private val nearby = NearbyCandidates()
    private var aimRay: PointingRay? = null
    var activeDetector: String = "YOLO26n-seg"
    var activeDepthBackend: String = "YOLO26-Depth"
    var lastObjectMs: Long = 0L
    var lastDepthMs: Long = 0L
    var lastHandMs: Long = 0L

    fun setPipelineInfo(
        detector: String,
        depthBackend: String,
        objectMs: Long,
        depthMs: Long,
        handMs: Long,
    ) {
        activeDetector = detector
        activeDepthBackend = depthBackend
        lastObjectMs = objectMs
        lastDepthMs = depthMs
        lastHandMs = handMs
        postInvalidate()
    }
    private val rayPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(3f)
        color = 0xFF00E5FF.toInt()
    }
    private var probeLines: List<String> = emptyList()
    private var decoderFps: Float = 0f
    private val hudText = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.WHITE
        textSize = dp(12f)
        isFakeBoldText = true
    }
    private val badge = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = 0xE61B5E20.toInt()
    }
    private val cross = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(2f)
        color = 0xFF00E5FF.toInt()
    }
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

    fun setDecoderFps(fps: Float) {
        decoderFps = fps
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

    fun selectedTrackId(): Int? = spatialEngine.currentLockedId

    @Synchronized
    fun groundingHint(): GroundingHint? {
        val output = spatialOutput ?: return null
        val ray = output.ray ?: return null
        if (spatialFrameWidth <= 0 || spatialFrameHeight <= 0) return null
        return GroundingHint(
            sequence = spatialSequence,
            frameWidth = spatialFrameWidth,
            frameHeight = spatialFrameHeight,
            tipX = ray.tipX,
            tipY = ray.tipY,
            dirX = ray.dirX,
            dirY = ray.dirY,
            eligible = output.handPose == SpatialTargetingEngine.HandPose.POINTING_INTENDED &&
                output.hitId == null && output.lockId == null &&
                output.state == SpatialTargetingEngine.State.POINTING,
        )
    }

    fun notificationDistance(): String = distanceEstimator.shortStatus(latest?.boxes.orEmpty())

    fun drawnBoxCount(): Int {
        val frame = latest ?: return 0
        val boxes = frame.boxes
        if (boxes.isEmpty()) return 0
        // Before the window is measured, keep the stored count. Once the view has
        // a size, a box outside the aspect-fit camera viewport is not drawn.
        if (width <= 0 || height <= 0 || frame.width <= 0 || frame.height <= 0) return boxes.size
        return boxes.count { CoverGeometry.isVisible(frame.width, frame.height, width, height, it) }
    }

    /**
     * Sees every detector object, including ones that are not the drawn box,
     * so a held point at a new center can switch the lock.
     */
    fun observeCandidates(frame: FrameDetections): String? {
        val output = evaluateSpatial(frame)
        aimRay = output.ray?.let { ray ->
            PointingRay(
                tipX = ray.tipX,
                tipY = ray.tipY,
                dirX = ray.dirX,
                dirY = ray.dirY,
                dirZ = ray.dirZ,
                reach = ray.reach,
                joints = ray.joints,
                reversible = false,
            )
        }
        invalidate()
        return null
    }

    fun updateFrame(frame: FrameDetections): String? {
        val previous = latest
        val spatialOutput = evaluateSpatial(frame)
        val lockedId = spatialOutput.lockId
        val aimId = spatialOutput.hitId
        val ray = spatialOutput.ray
        aimRay = if (ray != null) {
            PointingRay(
                tipX = ray.tipX,
                tipY = ray.tipY,
                dirX = ray.dirX,
                dirY = ray.dirY,
                dirZ = ray.dirZ,
                reach = ray.reach,
                joints = ray.joints,
                reversible = false,
            )
        } else null

        val incomingHands = HandTrack.forDisplay(frame.boxes)
        val incomingObjects = if (frame.boxes.isEmpty()) {
            emptyList()
        } else {
            val objs = frame.boxes.filter { it.kind == "object" }
            if (lockedId != null) {
                objs.sortedByDescending { it.trackId == lockedId }
            } else if (aimId != null) {
                objs.sortedByDescending { it.trackId == aimId }
            } else {
                objs
            }
        }
        val incoming = HandTrack.compose(incomingObjects, incomingHands)
        val now = SystemClock.elapsedRealtime()
        val accepted = OverlayDiagnostics.acceptFrame(
            frameSequence = frame.sequence,
            shownSequence = shownSequence,
            incomingBoxes = incoming.size,
            showingTrackId = previous?.boxes?.firstOrNull { it.kind == "object" }?.trackId
                ?: previous?.boxes?.firstOrNull()?.trackId,
            incomingTrackId = incomingObjects.firstOrNull()?.trackId,
            lockedId = lockedId,
            captureIsRecent = OverlayDiagnostics.captureIsRecent(now, frame.capturedAtMs, BoxHold.HOLD_MS),
        )
        if (!accepted) return null
        shownSequence = OverlayDiagnostics.nextShownSequence(shownSequence, frame.sequence, accepted = true)
        val ageMs = if (receivedAt == 0L) Long.MAX_VALUE else now - receivedAt
        val previousObjects = HandTrack.objectsOnly(previous?.boxes.orEmpty())
        val keptObjects = BoxHold.keep(previousObjects, incomingObjects, ageMs)
        val keptHands = incomingHands
        val kept = HandTrack.compose(keptObjects, keptHands)
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
        spatialEngine.reset()
        spatialSequence = Long.MIN_VALUE
        spatialOutput = null
        spatialFrameWidth = 0
        spatialFrameHeight = 0
        aimRay = null
        invalidate()
    }

    @Synchronized
    private fun evaluateSpatial(frame: FrameDetections): SpatialTargetingEngine.Output {
        if (frame.sequence == spatialSequence) {
            spatialOutput?.let { return it }
        }
        val output = spatialEngine.step(frame)
        spatialSequence = frame.sequence
        spatialOutput = output
        spatialFrameWidth = frame.width
        spatialFrameHeight = frame.height
        return output
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        if (w > 0 && h > 0 && latest?.boxes?.isNotEmpty() == true) invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        draws.incrementAndGet()
        val frame = latest
        val hud = frame?.let {
            TrainingHud.status(
                it,
                spatialEngine.currentLockedId,
                aimRay,
                detector = activeDetector,
                depthBackend = activeDepthBackend,
                objectMs = lastObjectMs,
                depthMs = lastDepthMs,
                handMs = lastHandMs,
                strict3DMode = strict3DMode,
                lockProvenance = spatialOutput?.lockProvenance ?: "no_target",
            )
        }
        drawHud(canvas, hud)
        drawProbe(canvas)
        if (frame == null) return
        if (width <= 0 || height <= 0 || frame.width <= 0 || frame.height <= 0) {
            if (width <= 0 || height <= 0) {
                post {
                    if (width > 0 && height > 0 && latest?.boxes?.isNotEmpty() == true) invalidate()
                }
            }
            return
        }
        val liveRay = aimRay
        val spatialTarget = spatialOutput?.lockId?.let { id -> frame.boxes.firstOrNull { it.trackId == id } }
            ?: spatialOutput?.hitId?.let { id -> frame.boxes.firstOrNull { it.trackId == id } }
        drawAim(canvas, frame, liveRay, spatialTarget)
        for (observation in frame.boxes) {
            if (observation.kind == "hand") {
                drawHand(canvas, frame, observation, hud?.pointing == true)
                continue
            }
            val current = CoverGeometry.project(frame.width, frame.height, width, height, observation)
                .let { RectF(it.left, it.top, it.right, it.bottom) }
            val color = when (observation.kind) {
                "face" -> 0xFF00D9FF.toInt()
                "arm" -> 0x66FFB020.toInt()
                else -> 0xFF80F45D.toInt()
            }
            val locked = spatialEngine.currentLockedId != null && observation.trackId == spatialEngine.currentLockedId
            val hovered = aimRay != null && spatialEngine.currentHoverId != null && observation.trackId == spatialEngine.currentHoverId
            stroke.color = if (locked) Color.WHITE else if (hovered) 0xFFFFD700.toInt() else color
            stroke.strokeWidth = if (locked) dp(5f) else dp(2.5f)
            canvas.drawRoundRect(current, dp(7f), dp(7f), stroke)

            // Draw instance segmentation mask contour polygon if available
            if (observation.points.isNotEmpty() && observation.kind == "object") {
                handPath.reset()
                val p0 = observation.points[0]
                val first = CoverGeometry.projectPoint(frame.width, frame.height, width, height, p0.first, p0.second)
                handPath.moveTo(first.first, first.second)
                for (idx in 1 until observation.points.size) {
                    val pt = observation.points[idx]
                    val at = CoverGeometry.projectPoint(frame.width, frame.height, width, height, pt.first, pt.second)
                    handPath.lineTo(at.first, at.second)
                }
                handPath.close()
                canvas.drawPath(handPath, stroke)
            }

            val feet = distanceEstimator.estimate(frame.height, observation, frame.sequence)
            val distanceLabel = if (feet != null) {
                " ~%.1f ft".format(java.util.Locale.US, feet)
            } else ""
            val lockTag = if (locked) " [LOCKED]" else ""
            val label = observation.label + " #" + observation.trackId + distanceLabel + lockTag
            val labelW = text.measureText(label) + dp(12f)
            val labelTop = (current.top - dp(25f)).coerceAtLeast(0f)
            canvas.drawRoundRect(
                current.left, labelTop, current.left + labelW,
                labelTop + dp(23f), dp(4f), dp(4f), background
            )
            canvas.drawText(label, current.left + dp(6f), labelTop + dp(16f), text)
        }
    }

    private fun drawHand(
        canvas: Canvas,
        frame: FrameDetections,
        observation: BoxObservation,
        pointing: Boolean,
    ) {
        val cyan = 0xFF00E5FF.toInt()
        val outline = if (pointing) 0xFF80F45D.toInt() else cyan
        point.color = cyan
        handLine.color = cyan
        handOutline.color = outline
        fun mapped(x: Float, y: Float) =
            CoverGeometry.projectPoint(frame.width, frame.height, width, height, x, y)
        val hull = HandOutline.hull(observation.points)
        if (hull.size >= 3) {
            handPath.reset()
            val first = mapped(hull[0].first, hull[0].second)
            handPath.moveTo(first.first, first.second)
            for (index in 1 until hull.size) {
                val at = mapped(hull[index].first, hull[index].second)
                handPath.lineTo(at.first, at.second)
            }
            handPath.close()
            canvas.drawPath(handPath, handOutline)
        }
        HandSkeleton.connections.forEach { (from, to) ->
            val start = observation.points.getOrNull(from) ?: return@forEach
            val end = observation.points.getOrNull(to) ?: return@forEach
            canvas.drawLine(
                mapped(start.first, start.second).first,
                mapped(start.first, start.second).second,
                mapped(end.first, end.second).first,
                mapped(end.first, end.second).second,
                handLine,
            )
        }
        observation.points.forEachIndexed { index, (x, y) ->
            if (index == IndexPoint.INDEX_TIP) return@forEachIndexed
            val at = mapped(x, y)
            canvas.drawCircle(at.first, at.second, dp(3f), point)
        }
        val tip = HandTrack.indexTip(observation)
        if (tip != null) {
            val at = mapped(tip.first, tip.second)
            canvas.drawCircle(at.first, at.second, dp(9f), indexFill)
            canvas.drawCircle(at.first, at.second, dp(9f), indexRing)
        }
        val wrist = observation.points.getOrNull(IndexPoint.WRIST)
        if (wrist != null) {
            val at = mapped(wrist.first, wrist.second)
            val title = if (pointing) TrainingHud.POINTING else TrainingHud.HAND
            val w = text.measureText(title) + dp(12f)
            val left = at.first
            val top = (at.second + dp(10f)).coerceAtMost(height - dp(24f))
            canvas.drawRoundRect(left, top, left + w, top + dp(22f), dp(4f), dp(4f), badge)
            canvas.drawText(title, left + dp(6f), top + dp(16f), text)
        }
    }

    private fun drawAim(
        canvas: Canvas,
        frame: FrameDetections,
        ray: PointingRay?,
        target: BoxObservation?,
    ) {
        if (ray == null) return
        if (frame.boxes.none { it.kind == "hand" && !it.held && it.points.size >= 21 }) return
        fun px(x: Float, y: Float) =
            CoverGeometry.projectPoint(frame.width, frame.height, width, height, x, y)
        val start = px(ray.tipX, ray.tipY)
        val endPoint = if (target != null) {
            (target.left + target.right) / 2f to (target.top + target.bottom) / 2f
        } else {
            val span = kotlin.math.hypot(frame.width.toFloat(), frame.height.toFloat()) * 0.95f
            val rawX = ray.tipX + ray.dirX * span
            val rawY = ray.tipY + ray.dirY * span
            clipToFrame(ray.tipX, ray.tipY, rawX, rawY, frame.width.toFloat(), frame.height.toFloat())
        }
        val end = px(endPoint.first, endPoint.second)
        val steps = 8
        var previousX = start.first
        var previousY = start.second
        for (step in 1..steps) {
            val t = step / steps.toFloat()
            val x = start.first + (end.first - start.first) * t
            val y = start.second + (end.second - start.second) * t
            val intoScene = if (ray.dirZ > 0f) ray.dirZ else kotlin.math.abs(ray.dirZ) * 0.5f
            rayPaint.strokeWidth = dp(5.5f) * (1f - 0.72f * t * (0.35f + intoScene).coerceAtMost(1f))
            canvas.drawLine(previousX, previousY, x, y, rayPaint)
            previousX = x
            previousY = y
        }
        rayPaint.strokeWidth = dp(3f)
        canvas.drawCircle(end.first, end.second, dp(4f), indexFill)
        if (target == null) {
            val arm = dp(10f)
            canvas.drawLine(end.first - arm, end.second, end.first + arm, end.second, cross)
            canvas.drawLine(end.first, end.second - arm, end.first, end.second + arm, cross)
        }
    }

    private fun drawHud(canvas: Canvas, hud: TrainingHud.Status?) {
        if (width <= 0) return
        val lines = if (hud == null) {
            listOf("Hands: 0", "State: --", "Target: none")
        } else {
            TrainingHud.lines(hud, decoderFps)
        }
        val lineH = dp(16f)
        val pad = dp(8f)
        var maxW = dp(120f)
        for (line in lines) maxW = maxOf(maxW, hudText.measureText(line))
        val right = width - dp(12f)
        val left = (right - maxW - pad * 2).coerceAtLeast(dp(12f))
        val top = dp(12f)
        val box = RectF(left, top, right, top + pad * 2 + lineH * lines.size)
        canvas.drawRoundRect(box, dp(6f), dp(6f), background)
        stroke.color = 0xFF00E5FF.toInt()
        canvas.drawRoundRect(box, dp(6f), dp(6f), stroke)
        lines.forEachIndexed { index, line ->
            canvas.drawText(line, left + pad, top + pad + lineH * (index + 1) - dp(3f), hudText)
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

    private fun clipToFrame(
        x0: Float,
        y0: Float,
        x1: Float,
        y1: Float,
        width: Float,
        height: Float,
    ): Pair<Float, Float> {
        var x = x1
        var y = y1
        val dx = x1 - x0
        val dy = y1 - y0
        if (kotlin.math.abs(dx) > 1e-3f) {
            if (x < 0f) {
                y = y0 + dy * (0f - x0) / dx
                x = 0f
            } else if (x > width) {
                y = y0 + dy * (width - x0) / dx
                x = width
            }
        }
        if (kotlin.math.abs(dy) > 1e-3f) {
            if (y < 0f) {
                x = x0 + dx * (0f - y0) / dy
                y = 0f
            } else if (y > height) {
                x = x0 + dx * (height - y0) / dy
                y = height
            }
        }
        return x.coerceIn(0f, width) to y.coerceIn(0f, height)
    }

    private fun dp(value: Float) = value * resources.displayMetrics.density
}
