package com.mattumali.visortracker

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ComponentCallbacks
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.service.quicksettings.TileService
import android.view.Gravity
import android.view.WindowManager
import com.macedonga.visor.kit.LiveVideoDecoder
import com.macedonga.visor.kit.Visor
import com.macedonga.visor.kit.protocol.LiveConfig
import com.macedonga.visor.kit.protocol.LiveQuality
import com.macedonga.visor.kit.protocol.Scope
import java.util.ArrayDeque
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * User-started foreground service: joins Visor's existing full-rate camera.live feed.
 *
 * No camera server, no second phone camera session, no JPEG polling and no user
 * token sent over the network. The VDK requires a one-time CAMERA scope grant.
 * Decoding happens on an IO coroutine; inference runs on a separate worker so
 * slow AI never blocks the video decoder. A frame already processing is dropped.
 */
class OverlayTrackingService : Service() {
    private val mainHandler = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private val motionWorker = Executors.newSingleThreadExecutor()
    private val motionBusy = AtomicBoolean(false)
    private val motionTracker = MotionTracker()
    private val pendingDetection = AtomicReference<DetectionBaseline?>(null)
    private val frameSequence = AtomicLong(0)
    private val grayQueue = ArrayDeque<GrayFrame>()
    private val grayLock = Any()
    private val motionHistory = ArrayDeque<GrayFrame>()
    private data class DetectionBaseline(val detections: FrameDetections, val gray: GrayFrame)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val processing = AtomicBoolean(false)
    private val decodedFrames = AtomicInteger(0)
    private val inferenceFrames = AtomicInteger(0)
    private val motionFrames = AtomicInteger(0)
    private val stats = PipelineStats()
    private val windows by lazy { getSystemService(Context.WINDOW_SERVICE) as WindowManager }

    private var overlay: TrackingOverlayView? = null
    private var pipeline: VisionPipeline? = null
    private var visor: Visor? = null
    private var streamJob: Job? = null
    private var running = false
    private var includeHandsFaces = false
    private var preset = DetectionPreset.FAST
    private var lastOfferedAtMs = 0L
    private var lastReportAtMs = 0L
    private var lastBoxCount = 0
    private var streamBeginMs = 0L

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_CALIBRATE) {
            val measured = intent.getFloatExtra(EXTRA_REFERENCE_FEET, 0f)
            if (running) {
                report(overlay?.calibrateDistance(measured) ?: "Overlay not ready")
            } else {
                lastStatus = "Start tracking and Live View before calibrating"
                stopSelf(startId)
            }
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_CLEAR_DISTANCE) {
            if (running) {
                overlay?.clearDistance()
                report("Distance calibration cleared")
            } else {
                lastStatus = "Distance measurement not running"
                stopSelf(startId)
            }
            return START_NOT_STICKY
        }
        if (running) return START_NOT_STICKY
        if (!Settings.canDrawOverlays(this)) {
            lastStatus = "Grant Android Display over other apps permission first"
            stopSelf()
            return START_NOT_STICKY
        }
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        includeHandsFaces = intent?.getBooleanExtra(
            EXTRA_ALL_DETECTORS, prefs.getBoolean(PREF_HANDS_FACES, false)
        ) ?: prefs.getBoolean(PREF_HANDS_FACES, false)
        preset = DetectionPreset.read(
            intent?.getStringExtra(EXTRA_PRESET) ?: prefs.getString(PREF_PRESET, null)
        )

        createChannel()
        startForeground(NOTIFICATION_ID, notification("Preparing VDK camera overlay"))
        try {
            val view = TrackingOverlayView(this)
            windows.addView(view, overlayLayout())
            overlay = view
            applicationContext.registerComponentCallbacks(displayCallbacks)
        } catch (error: Exception) {
            lastStatus = "Overlay failed: " + (error.message ?: "unknown")
            stopSelf()
            return START_NOT_STICKY
        }

        running = true
        active = true
        refreshTile()
        report("Loading " + preset.name.lowercase() + " detector (VDK video up to 30 FPS)")
        worker.execute {
            try {
                val ready = VisionPipeline(applicationContext, includeHandsFaces, preset) {
                    overlay?.selectedTrackId()
                }
                mainHandler.post {
                    if (!running) {
                        worker.execute { ready.close() }
                    } else {
                        pipeline = ready
                        startVdk()
                    }
                }
            } catch (error: Exception) {
                mainHandler.post { report("MediaPipe failed: " + (error.message ?: "unknown")) }
            }
        }
        return START_NOT_STICKY
    }

    private fun startVdk() {
        streamJob = scope.launch {
            try {
                report("Connecting to Visor camera.live")
                val linked = Visor.connect(applicationContext)
                visor = linked
                if (Scope.CAMERA !in linked.scopes()) {
                    report("Visor camera access NOT GRANTED. Open Tracker and tap Grant camera access.")
                    return@launch
                }
                if (!running) return@launch

                lastOfferedAtMs = 0L
                lastReportAtMs = SystemClock.elapsedRealtime()
                streamBeginMs = lastReportAtMs
                decodedFrames.set(0)
                inferenceFrames.set(0)
                motionFrames.set(0)
                report("VDK connected; waiting for Visor Live View")

                val requested = LiveConfig {
                    quality = LiveQuality.LOW
                    frameRate = 30
                    audio = false
                }
                LiveVideoDecoder { image, _ ->
                    if (!running) return@LiveVideoDecoder
                    decodedFrames.incrementAndGet()
                    val now = SystemClock.elapsedRealtime()
                    val sequence = frameSequence.incrementAndGet()
                    // Copy pixels and return. The VDK suspends after 64 unread samples,
                    // so any matching or RGB work here makes boxes lag the Live View.
                    val gray = try {
                        GrayFrame.capture(image, sequence = sequence, capturedAtMs = now)
                    } catch (error: Exception) {
                        report("Grayscale extraction failed: " + (error.message ?: "unknown"))
                        null
                    }
                    if (gray != null) {
                        synchronized(grayLock) {
                            var dropped = 0
                            while (grayQueue.size >= MAX_GRAY_QUEUE) {
                                grayQueue.removeFirst()
                                dropped++
                            }
                            grayQueue.addLast(gray)
                            if (dropped > 0) stats.recordDiscard(dropped)
                        }
                        scheduleMotion()
                    }

                    val anchor = gray
                    val inferenceDue = anchor != null && now - lastOfferedAtMs >= MIN_INFERENCE_INTERVAL_MS
                    if (anchor != null && inferenceDue && processing.compareAndSet(false, true)) {
                        lastOfferedAtMs = now
                        try {
                            val snapshot = YuvSnapshot.capture(image)
                            worker.execute {
                                var bitmap: Bitmap? = null
                                try {
                                    if (!running) return@execute
                                    bitmap = snapshot.toBitmap(preset.maxFrameWidth)
                                    val detections = pipeline?.detect(bitmap) ?: return@execute
                                    inferenceFrames.incrementAndGet()
                                    val baseline = DetectionBaseline(
                                        detections.copy(sequence = sequence, capturedAtMs = now),
                                        anchor,
                                    )
                                    if (pendingDetection.getAndSet(baseline) != null) stats.recordDiscard()
                                    scheduleMotion()
                                } catch (error: Exception) {
                                    report("Decode/detection failed: " + (error.message ?: "unknown"))
                                } finally {
                                    bitmap?.recycle()
                                    processing.set(false)
                                }
                            }
                        } catch (error: Exception) {
                            processing.set(false)
                            report("Hardware frame conversion failed: " + (error.message ?: "unknown"))
                        }
                    } else if (inferenceDue) {
                        stats.recordDiscard()
                    }
                    if (now - lastReportAtMs >= REPORT_MS) {
                        val seconds = (now - lastReportAtMs).coerceAtLeast(1) / 1000f
                        val videoFps = decodedFrames.getAndSet(0) / seconds
                        val detectFps = inferenceFrames.getAndSet(0) / seconds
                        val view = overlay
                        val boxFps = (view?.consumeBoxUpdates() ?: motionFrames.getAndSet(0)) / seconds
                        val drawFps = (view?.consumeDraws() ?: 0) / seconds
                        val age = view?.frameAgeMs ?: 0
                        val window = stats.snapshotAndReset()
                        lastReportAtMs = now
                        report(PipelineStats.formatReport(
                            videoFps, detectFps, boxFps, drawFps,
                            age, window.p95AgeMs, window.discarded, lastBoxCount))
                    }
                }.use { decoder ->
                    // Visor serves the very same video being shown in its own Live View.
                    // Collect on IO. Each sample feeds Android's hardware MediaCodec.
                    linked.camera.live(requested).collect { sample ->
                        if (!running) return@collect
                        decoder.decode(sample)
                    }
                }
                if (running) report("Live View ended; open it in Visor to restart")
            } catch (_: CancellationException) {
                // User stopped the overlay.
            } catch (error: Exception) {
                if (running) report("VDK stream failed: " + (error.message ?: error.javaClass.simpleName))
            } finally {
                visor?.close()
                visor = null
                mainHandler.post { overlay?.clear() }
            }
        }
    }

    private fun scheduleMotion() {
        if (!motionBusy.compareAndSet(false, true)) return
        motionWorker.execute {
            try {
                while (running) {
                    val batch = synchronized(grayLock) {
                        if (grayQueue.isEmpty()) emptyList() else grayQueue.toList().also { grayQueue.clear() }
                    }
                    for (frame in batch) rememberFrame(frame)
                    val pending = pendingDetection.getAndSet(null)
                    if (pending != null && canRebase(pending)) {
                        rebase(pending)
                    } else if (pending != null) {
                        // The grayscale anchor aged out of the short history.
                        // Show this detector result anyway. Replaying a gap
                        // would keep the previous coordinates on screen.
                        stats.recordDiscard()
                        motionTracker.reset(pending.detections, pending.gray)
                        var latest = pending.detections
                        for (frame in batch) {
                            if (frame.sequence <= pending.gray.sequence) continue
                            val stepped = motionTracker.advance(frame) ?: break
                            latest = stepped
                        }
                        publish(latest)
                    } else {
                        for (frame in batch) publishAdvance(frame)
                    }
                    val more = synchronized(grayLock) { grayQueue.isNotEmpty() } || pendingDetection.get() != null
                    if (!more) break
                }
            } catch (error: Exception) {
                report("Motion tracking failed: " + (error.message ?: "unknown"))
            } finally {
                motionBusy.set(false)
            }
            val more = running && (synchronized(grayLock) { grayQueue.isNotEmpty() } || pendingDetection.get() != null)
            if (more) scheduleMotion()
        }
    }

    private fun rememberFrame(frame: GrayFrame) {
        motionHistory.addLast(frame)
        while (motionHistory.size > MAX_GRAY_QUEUE) motionHistory.removeFirst()
    }

    /** A detector result is usable when its grayscale frame is still in the short history. */
    private fun canRebase(pending: DetectionBaseline): Boolean {
        if (motionHistory.isEmpty()) return true
        return pending.gray.sequence >= motionHistory.first().sequence
    }

    private fun rebase(pending: DetectionBaseline) {
        motionTracker.reset(pending.detections, pending.gray)
        var latest: FrameDetections? = null
        for (frame in motionHistory) {
            if (frame.sequence <= pending.gray.sequence) continue
            val stepped = motionTracker.advance(frame) ?: break
            latest = stepped
        }
        publish(latest ?: pending.detections)
    }

    private fun publishAdvance(frame: GrayFrame) {
        val stepped = motionTracker.advance(frame)
        if (stepped == null) {
            if (lastBoxCount > 0) {
                lastBoxCount = 0
                val sequence = frame.sequence
                mainHandler.post { if (running) overlay?.clearUpTo(sequence) }
            }
        } else {
            publish(stepped)
        }
    }

    private fun publish(frame: FrameDetections) {
        lastBoxCount = frame.boxes.count { it.kind == "object" }
        if (frame.capturedAtMs > 0L) {
            stats.recordAge((SystemClock.elapsedRealtime() - frame.capturedAtMs).toInt().coerceAtLeast(0))
        }
        mainHandler.post { if (running) overlay?.updateFrame(frame) }
    }

    private val displayCallbacks = object : ComponentCallbacks {
        override fun onConfigurationChanged(newConfig: Configuration) {
            val view = overlay ?: return
            try {
                windows.updateViewLayout(view, overlayLayout())
            } catch (_: Exception) { }
        }
        override fun onLowMemory() = Unit
    }

    private fun overlayLayout(): WindowManager.LayoutParams {
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
                WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED or
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 0
            y = 0
            // Keep the window fully opaque. Samsung composites a translucent overlay
            // under Visor's video, which leaves the boxes on the Tracker screen only.
            alpha = 1f
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                layoutInDisplayCutoutMode =
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                fitInsetsTypes = 0
                setFitInsetsIgnoringVisibility(true)
            }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val bounds = windows.currentWindowMetrics.bounds
            params.width = bounds.width()
            params.height = bounds.height()
        }
        return params
    }

    private fun report(message: String) {
        lastStatus = message
        if (!running) return
        mainHandler.post {
            if (running) (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .notify(NOTIFICATION_ID, notification(message))
        }
    }

    private fun createChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(
            CHANNEL_ID, "Live tracking FPS", NotificationManager.IMPORTANCE_LOW))
    }

    private fun notification(line: String): Notification {
        val open = PendingIntent.getActivity(
            this, 1, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val stop = PendingIntent.getService(
            this, 2, Intent(this, OverlayTrackingService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val referenceFeet = getSharedPreferences(PREFS, MODE_PRIVATE)
            .getString("reference_distance_feet", "6")?.toFloatOrNull() ?: 6f
        val calibrate = PendingIntent.getService(
            this, 3, Intent(this, OverlayTrackingService::class.java)
                .setAction(ACTION_CALIBRATE)
                .putExtra(EXTRA_REFERENCE_FEET, referenceFeet),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentTitle("Visor VDK tracking")
            .setContentText(line)
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(android.R.drawable.ic_menu_compass, "Calibrate", calibrate)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stop)
            .build()
    }

    override fun onDestroy() {
        running = false
        active = false
        refreshTile()
        streamJob?.cancel()
        scope.cancel()
        visor?.close()
        visor = null
        try { applicationContext.unregisterComponentCallbacks(displayCallbacks) } catch (_: Exception) { }
        synchronized(grayLock) { grayQueue.clear() }
        overlay?.let {
            it.clear()
            try { windows.removeViewImmediate(it) } catch (_: Exception) { }
        }
        overlay = null
        val old = pipeline
        pipeline = null
        if (old != null) worker.execute { old.close() }
        worker.shutdown()
        motionWorker.shutdown()
        pendingDetection.set(null)
        lastStatus = "Overlay stopped"
        super.onDestroy()
    }

    private fun refreshTile() {
        try {
            TileService.requestListeningState(
                this, ComponentName(this, TrackerQuickTile::class.java)
            )
        } catch (_: Exception) { /* The tile is optional; never break tracking. */ }
    }

    companion object {
        const val PREFS = "tracker"
        const val PREF_PRESET = "detector_preset"
        const val PREF_HANDS_FACES = "detect_hands_faces"
        const val EXTRA_PRESET = "preset"
        const val ACTION_START = "com.mattumali.visortracker.START_OVERLAY"
        const val ACTION_STOP = "com.mattumali.visortracker.STOP_OVERLAY"
        const val ACTION_CALIBRATE = "com.mattumali.visortracker.CALIBRATE_DISTANCE"
        const val ACTION_CLEAR_DISTANCE = "com.mattumali.visortracker.CLEAR_DISTANCE"
        const val EXTRA_REFERENCE_FEET = "measured_reference_feet"
        const val EXTRA_ALL_DETECTORS = "all_detectors"
        private const val CHANNEL_ID = "visor_tracker_high_fps"
        private const val NOTIFICATION_ID = 4421
        private const val MIN_INFERENCE_INTERVAL_MS = 66L // Up to ~15 detections/sec if hardware permits.
        private const val REPORT_MS = 4000L
        private const val MAX_GRAY_QUEUE = 30
        @Volatile var active: Boolean = false
            private set
        @Volatile var lastStatus: String = "Overlay not started"
            private set
    }
}
