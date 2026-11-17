package com.mattumali.visortracker

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.ComponentName
import android.service.quicksettings.TileService
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
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
    private data class DetectionBaseline(val detections: FrameDetections, val gray: GrayFrame)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val processing = AtomicBoolean(false)
    private val decodedFrames = AtomicInteger(0)
    private val inferenceFrames = AtomicInteger(0)
    private val motionFrames = AtomicInteger(0)
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
            val params = WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT
            ).apply {
                gravity = Gravity.TOP or Gravity.START
                alpha = 0.75f
            }
            windows.addView(view, params)
            overlay = view
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

                    // Fast gray-only tracking follows objects between AI inference results.
                    // It also runs when the detector is busy, but its single-slot worker
                    // never accumulates stale frames behind the video.
                    var gray: GrayFrame? = null
                    if (motionBusy.compareAndSet(false, true)) {
                        try {
                            gray = GrayFrame.capture(image)
                            val motionInput = gray
                            motionWorker.execute {
                                try {
                                    pendingDetection.getAndSet(null)?.let {
                                        motionTracker.reset(it.detections, it.gray)
                                    }
                                    motionTracker.advance(motionInput)?.let { predicted ->
                                        motionFrames.incrementAndGet()
                                        mainHandler.post {
                                            if (running) overlay?.updateFrame(predicted)
                                        }
                                    }
                                } catch (error: Exception) {
                                    report("Motion tracking failed: " + (error.message ?: "unknown"))
                                } finally {
                                    motionBusy.set(false)
                                }
                            }
                        } catch (error: Exception) {
                            motionBusy.set(false)
                            report("Grayscale extraction failed: " + (error.message ?: "unknown"))
                        }
                    }

                    // The full AI detector is slower; never delay the hardware decoder.
                    if (now - lastOfferedAtMs >= MIN_INFERENCE_INTERVAL_MS &&
                        processing.compareAndSet(false, true)) {
                        lastOfferedAtMs = now
                        try {
                            val baselineGray = gray ?: GrayFrame.capture(image)
                            val snapshot = YuvSnapshot.capture(image)
                            worker.execute {
                                var bitmap: Bitmap? = null
                                try {
                                    if (!running) return@execute
                                    bitmap = snapshot.toBitmap(preset.maxFrameWidth)
                                    val detections = pipeline?.detect(bitmap) ?: return@execute
                                    inferenceFrames.incrementAndGet()
                                    lastBoxCount = detections.boxes.size
                                    pendingDetection.set(DetectionBaseline(detections, baselineGray))
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
                    }
                    if (now - lastReportAtMs >= REPORT_MS) {
                        val seconds = (now - lastReportAtMs).coerceAtLeast(1) / 1000f
                        val videoFps = decodedFrames.getAndSet(0) / seconds
                        val detectFps = inferenceFrames.getAndSet(0) / seconds
                        val trackingFps = motionFrames.getAndSet(0) / seconds
                        lastReportAtMs = now
                        report("Video %.1f FPS | AI %.1f FPS | boxes %.1f FPS | %d objects".format(
                            videoFps, detectFps, trackingFps, lastBoxCount))
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
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentTitle("Visor VDK tracking")
            .setContentText(line)
            .setContentIntent(open)
            .setOngoing(true)
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
        const val EXTRA_ALL_DETECTORS = "all_detectors"
        private const val CHANNEL_ID = "visor_tracker_high_fps"
        private const val NOTIFICATION_ID = 4421
        private const val MIN_INFERENCE_INTERVAL_MS = 66L // Up to ~15 detections/sec if hardware permits.
        private const val REPORT_MS = 4000L
        @Volatile var active: Boolean = false
            private set
        @Volatile var lastStatus: String = "Overlay not started"
            private set
    }
}
