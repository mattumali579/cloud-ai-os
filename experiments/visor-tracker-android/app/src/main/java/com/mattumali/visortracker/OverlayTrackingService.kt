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
import android.util.Log
import android.provider.Settings
import android.service.quicksettings.TileService
import android.view.Gravity
import android.view.WindowManager
import com.macedonga.visor.kit.LiveVideoDecoder
import com.macedonga.visor.kit.Visor
import com.macedonga.visor.kit.protocol.LiveConfig
import com.macedonga.visor.kit.protocol.LiveQuality
import com.macedonga.visor.kit.protocol.Scope
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
 * Decoding happens on an IO coroutine. The motion tracker steps the newest gray
 * frame. Object recognition stays at 5 Hz before a lock and 4 Hz after it, on a
 * separate worker, so a slow detection cannot block the camera frames.
 */
class OverlayTrackingService : Service() {
    private val mainHandler = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private val motionWorker = Executors.newSingleThreadExecutor()
    private val motionBusy = AtomicBoolean(false)
    private val handoff = TrackingHandoff()
    private val pendingDetection = AtomicReference<DetectionBaseline?>(null)
    private val frameSequence = AtomicLong(0)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val processing = AtomicBoolean(false)
    private val decodedFrames = AtomicInteger(0)
    private val inferenceFrames = AtomicInteger(0)
    private val totalFrames = AtomicInteger(0)
    private val grayFrames = AtomicInteger(0)
    private val detectorBusy = AtomicBoolean(false)
    private val stageFaults = StageFaults()
    private val totalInferences = AtomicInteger(0)
    private val rawDetections = AtomicInteger(0)
    private val stages = StageTimings()
    // The service context ties the window to this app's task. Samsung then
    // shows the boxes on the Tracker screen only. The application context
    // keeps a TYPE_APPLICATION_OVERLAY above Visor after the user switches apps.
    private val windows by lazy {
        applicationContext.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    }

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
    private var overlayAttached = false
    private var overlayError: String? = null
    private var cameraGranted: Boolean? = null
    private var liveConnected = false
    private var lastFailure: String? = null
    private var lastProgress = ""
    private var lastMetrics = ""
    private var decoderFps = 0f
    private var aiFps = 0f
    private var versionName = "?"
    private var versionCode = 0

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
        readVersion()
        if (!Settings.canDrawOverlays(this)) {
            lastFailure = "Display over other apps is denied"
            renderStatus()
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
        startForeground(NOTIFICATION_ID, notification("Attaching the Live View proof mark"))
        try {
            val view = TrackingOverlayView(applicationContext)
            windows.addView(view, overlayLayout())
            overlay = view
            overlayAttached = true
            overlayError = null
            applicationContext.registerComponentCallbacks(displayCallbacks)
        } catch (error: Exception) {
            overlayAttached = false
            overlayError = error.message ?: error.javaClass.simpleName
            renderStatus()
            stopSelf()
            return START_NOT_STICKY
        }

        running = true
        active = true
        refreshTile()
        mainHandler.removeCallbacks(diagnosticTick)
        mainHandler.post(diagnosticTick)
        report("Loading " + preset.name.lowercase() + " detector (VDK video up to 30 FPS)")
        worker.execute {
            try {
                val ready = VisionPipeline(applicationContext, includeHandsFaces, preset)
                mainHandler.post {
                    if (!running) {
                        worker.execute { ready.close() }
                    } else {
                        pipeline = ready
                        startVdk()
                    }
                }
            } catch (error: Exception) {
                mainHandler.post { report("MediaPipe failed: " + (error.message ?: "unknown"), failure = true) }
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
                cameraGranted = Scope.CAMERA in linked.scopes()
                renderStatus()
                if (cameraGranted != true) {
                    report(
                        "Visor camera access NOT GRANTED. Open Tracker and tap Grant camera access.",
                        failure = true,
                    )
                    return@launch
                }
                if (!running) return@launch

                lastOfferedAtMs = 0L
                lastReportAtMs = SystemClock.elapsedRealtime()
                streamBeginMs = lastReportAtMs
                decodedFrames.set(0)
                inferenceFrames.set(0)
                handoff.reset()
                handoff.metrics.snapshotAndReset()
                liveConnected = true
                report("VDK connected; waiting for Visor Live View")

                val requested = LiveConfig {
                    quality = LiveQuality.LOW
                    frameRate = 30
                    audio = false
                }
                LiveVideoDecoder { image, _ ->
                    if (!running) return@LiveVideoDecoder
                    decodedFrames.incrementAndGet()
                    totalFrames.incrementAndGet()
                    val now = SystemClock.elapsedRealtime()
                    val sequence = frameSequence.incrementAndGet()
                    // Copy pixels and return. The VDK suspends after 64 unread samples,
                    // so any matching or RGB work here makes boxes lag the Live View.
                    val grayStarted = SystemClock.elapsedRealtime()
                    val gray = try {
                        GrayFrame.capture(image, sequence = sequence, capturedAtMs = now)
                    } catch (error: Exception) {
                        stageFail("gray", "Grayscale extraction failed: " + (error.message ?: "unknown"))
                        null
                    }
                    if (gray != null) {
                        stages.record("g", SystemClock.elapsedRealtime() - grayStarted)
                        stageFaults.clear("gray")
                        refreshFaults()
                        grayFrames.incrementAndGet()
                        handoff.offer(gray)
                        scheduleMotion()
                    }

                    val anchor = gray
                    val locked = overlay?.selectedTrackId() != null
                    val inferenceDue = InferenceCadence.due(now, lastOfferedAtMs, locked)
                    if (DetectorSchedule.shouldStart(inferenceDue, !processing.get()) &&
                        processing.compareAndSet(false, true)
                    ) {
                        lastOfferedAtMs = now
                        try {
                            val snapshot = YuvSnapshot.capture(image)
                            detectorBusy.set(true)
                            worker.execute {
                                var bitmap: Bitmap? = null
                                try {
                                    if (!running) return@execute
                                    val yuvStarted = SystemClock.elapsedRealtime()
                                    bitmap = snapshot.toBitmap(preset.maxFrameWidth)
                                    stages.record("y", SystemClock.elapsedRealtime() - yuvStarted)
                                    stageFaults.clear("yuv")
                                    val aiStarted = SystemClock.elapsedRealtime()
                                    val detections = pipeline?.detect(bitmap) ?: return@execute
                                    stages.record("ai", SystemClock.elapsedRealtime() - aiStarted)
                                    stageFaults.clear("detect")
                                    refreshFaults()
                                    inferenceFrames.incrementAndGet()
                                    totalInferences.incrementAndGet()
                                    val objectCount = detections.boxes.count { it.kind == "object" }
                                    rawDetections.set(objectCount)
                                    Log.i(TAG, "detect objects=$objectCount seq=$sequence gray=${anchor != null}")
                                    val stamped = detections.copy(sequence = sequence, capturedAtMs = now)
                                    mainHandler.post {
                                        if (!running) return@post
                                        val note = overlay?.observeCandidates(stamped)
                                        if (!note.isNullOrBlank()) report(note)
                                        if (anchor == null) overlay?.updateFrame(stamped)
                                    }
                                    if (anchor != null) {
                                        val baseline = DetectionBaseline(stamped, anchor)
                                        if (pendingDetection.getAndSet(baseline) != null) {
                                            handoff.metrics.onStaleDetection()
                                        }
                                        scheduleMotion()
                                    }
                                } catch (error: Exception) {
                                    stageFail("detect", "Decode/detection failed: " + (error.message ?: "unknown"))
                                } finally {
                                    snapshot.releasePlanes()
                                    YuvSnapshot.recycleDetached(bitmap)
                                    detectorBusy.set(false)
                                    processing.set(false)
                                }
                            }
                        } catch (error: Exception) {
                            detectorBusy.set(false)
                            processing.set(false)
                            stageFail("yuv", "Hardware frame conversion failed: " + (error.message ?: "unknown"))
                        }
                    }
                    if (now - lastReportAtMs >= REPORT_MS) {
                        val seconds = (now - lastReportAtMs).coerceAtLeast(1) / 1000f
                        val videoFps = decodedFrames.getAndSet(0) / seconds
                        val detectFps = inferenceFrames.getAndSet(0) / seconds
                        decoderFps = videoFps
                        aiFps = detectFps
                        val view = overlay
                        val boxFps = (view?.consumeBoxUpdates() ?: 0) / seconds
                        val drawFps = (view?.consumeDraws() ?: 0) / seconds
                        val window = handoff.metrics.snapshotAndReset()
                        val trackFps = window.processed / seconds
                        lastReportAtMs = now
                        val metrics = PipelineStats.formatReport(
                            videoFps, detectFps, trackFps, boxFps, drawFps,
                            window, lastBoxCount, stageSuffix(),
                        )
                        val distance = view?.notificationDistance().orEmpty()
                        lastMetrics = if (distance.isEmpty()) metrics else "$metrics | $distance"
                        report(lastMetrics)
                    }
                }.use { decoder ->
                    // Visor serves the very same video being shown in its own Live View.
                    // Collect on IO. Each sample feeds Android's hardware MediaCodec.
                    linked.camera.live(requested).collect { sample ->
                        if (!running) return@collect
                        decoder.decode(sample)
                    }
                }
                liveConnected = false
                lastBoxCount = 0
                if (running) report("Live View ended; open it in Visor to restart", failure = true)
            } catch (_: CancellationException) {
                // User stopped the overlay.
            } catch (error: Exception) {
                liveConnected = false
                lastBoxCount = 0
                if (running) {
                    report(
                        "VDK stream failed: " + (error.message ?: error.javaClass.simpleName),
                        failure = true,
                    )
                }
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
                    val started = SystemClock.elapsedRealtime()
                    val pending = pendingDetection.getAndSet(null)
                    if (pending != null) recordDetectionAge(pending)
                    val event = handoff.pump(
                        SystemClock.elapsedRealtime(),
                        pending,
                        overlay?.selectedTrackId(),
                    )
                    stages.record("m", SystemClock.elapsedRealtime() - started)
                    if (event != null) publish(event.frame)
                    val more = handoff.hasPending() || pendingDetection.get() != null
                    if (!more) break
                }
            } catch (error: Exception) {
                report("Motion tracking failed: " + (error.message ?: "unknown"))
            } finally {
                motionBusy.set(false)
            }
            val more = running && (handoff.hasPending() || pendingDetection.get() != null)
            if (more) scheduleMotion()
        }
    }

    private fun detectionAgeMs(pending: DetectionBaseline): Long {
        if (pending.gray.capturedAtMs <= 0L) return 0L
        return (SystemClock.elapsedRealtime() - pending.gray.capturedAtMs).coerceAtLeast(0L)
    }

    private fun recordDetectionAge(pending: DetectionBaseline) {
        if (pending.gray.capturedAtMs > 0L) stages.record("d", detectionAgeMs(pending))
    }

    private fun stageSuffix(): String =
        "g${stages.median("g")} y${stages.median("y")} ai${stages.median("ai")} m${stages.median("m")} d${stages.median("d")}ms"

    private fun publish(frame: FrameDetections) {
        lastBoxCount = frame.boxes.count { it.kind == "object" }
        Log.i(TAG, "publish boxes=$lastBoxCount seq=${frame.sequence}")
        mainHandler.post {
            if (!running) return@post
            val note = overlay?.updateFrame(frame)
            if (!note.isNullOrBlank()) report(note)
        }
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

    private val diagnosticTick: Runnable = Runnable {
        if (!running) return@Runnable
        renderStatus()
        mainHandler.postDelayed(diagnosticTick, DIAGNOSTIC_MS)
    }

    private fun stageFail(stage: String, message: String) {
        stageFaults.fail(stage, message)
        refreshFaults()
        report(message)
    }

    private fun refreshFaults() {
        val summary = stageFaults.summary()
        lastFailure = summary.ifBlank { null }
    }

    private fun report(message: String, failure: Boolean = false) {
        if (failure) lastFailure = message
        lastProgress = message
        renderStatus()
    }

    private fun renderStatus() {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { renderStatus() }
            return
        }
        val snapshot = currentSnapshot()
        val diagnosis = OverlayDiagnostics.diagnose(snapshot)
        val extra = listOf(lastMetrics, lastProgress)
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .distinct()
            .joinToString(" | ")
        val line = OverlayDiagnostics.notificationLine(snapshot, diagnosis, extra)
        lastStatus = line
        overlay?.setProbe(OverlayDiagnostics.probeLines(snapshot, diagnosis))
        if (!running) return
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .notify(NOTIFICATION_ID, notification(line))
    }

    private fun currentSnapshot(): OverlaySnapshot {
        val probe = getSharedPreferences(PREFS, MODE_PRIVATE).getBoolean(PREF_PROBE, true)
        return OverlaySnapshot(
            versionName = versionName,
            versionCode = versionCode,
            packageName = packageName,
            overlayPermission = Settings.canDrawOverlays(this),
            serviceRunning = running,
            overlayAttached = overlayAttached && overlay != null,
            overlayError = overlayError,
            cameraGranted = cameraGranted,
            liveConnected = liveConnected,
            framesReceived = totalFrames.get(),
            grayFrames = grayFrames.get(),
            detectorBusy = detectorBusy.get(),
            inferencesCompleted = totalInferences.get(),
            rawDetections = rawDetections.get(),
            freshBoxes = lastBoxCount,
            drawnBoxes = overlay?.drawnBoxCount() ?: 0,
            boxAgeMs = overlay?.frameAgeMs ?: -1,
            decoderFps = decoderFps,
            aiFps = aiFps,
            failure = lastFailure,
            probeEnabled = probe,
        )
    }

    @Suppress("DEPRECATION")
    private fun readVersion() {
        val info = packageManager.getPackageInfo(packageName, 0)
        versionName = info.versionName ?: "?"
        versionCode = if (Build.VERSION.SDK_INT >= 28) info.longVersionCode.toInt() else info.versionCode
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
            .setContentTitle("Visor Tracker $versionName")
            .setContentText(line)
            .setStyle(Notification.BigTextStyle().bigText(line))
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(android.R.drawable.ic_menu_compass, "Calibrate", calibrate)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stop)
            .build()
    }

    override fun onDestroy() {
        val failed = overlayError != null || lastFailure != null
        running = false
        active = false
        mainHandler.removeCallbacks(diagnosticTick)
        refreshTile()
        streamJob?.cancel()
        scope.cancel()
        visor?.close()
        visor = null
        try { applicationContext.unregisterComponentCallbacks(displayCallbacks) } catch (_: Exception) { }
        handoff.reset()
        overlay?.let {
            it.clear()
            try { windows.removeViewImmediate(it) } catch (_: Exception) { }
        }
        overlay = null
        overlayAttached = false
        liveConnected = false
        val old = pipeline
        pipeline = null
        if (old != null) worker.execute { old.close() }
        worker.shutdown()
        motionWorker.shutdown()
        pendingDetection.set(null)
        if (!failed) lastStatus = "SERVICE STOPPED | Tracking service is not running"
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
        const val PREF_PROBE = "show_overlay_probe"
        const val EXTRA_PRESET = "preset"
        const val ACTION_START = "com.mattumali.visortracker.START_OVERLAY"
        const val ACTION_STOP = "com.mattumali.visortracker.STOP_OVERLAY"
        const val ACTION_CALIBRATE = "com.mattumali.visortracker.CALIBRATE_DISTANCE"
        const val ACTION_CLEAR_DISTANCE = "com.mattumali.visortracker.CLEAR_DISTANCE"
        const val EXTRA_REFERENCE_FEET = "measured_reference_feet"
        const val EXTRA_ALL_DETECTORS = "all_detectors"
        private const val CHANNEL_ID = "visor_tracker_high_fps"
        private const val NOTIFICATION_ID = 4421
        private const val TAG = "VisorTracker"
        private const val REPORT_MS = 4000L
        private const val DIAGNOSTIC_MS = 1000L
        @Volatile var active: Boolean = false
            private set
        @Volatile var lastStatus: String = "Overlay not started"
            private set
    }
}
