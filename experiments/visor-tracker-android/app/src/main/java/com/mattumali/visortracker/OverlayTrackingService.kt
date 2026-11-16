package com.mattumali.visortracker

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.PixelFormat
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Message
import android.os.Messenger
import android.os.RemoteException
import android.os.SystemClock
import android.provider.Settings
import android.view.Gravity
import android.view.WindowManager
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * User-started foreground service keeps the Visor frame subscription alive while
 * Visor itself is foregrounded. The overlay never requests screenshots of other apps
 * or intercepts touch. Camera frames never leave the phone.
 */
class OverlayTrackingService : Service() {
    private val mainHandler = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private val processing = AtomicBoolean(false)
    private val frameMessenger = Messenger(Handler(Looper.getMainLooper()) { msg ->
        onFrameMessage(msg)
        true
    })

    private val windows by lazy { getSystemService(Context.WINDOW_SERVICE) as WindowManager }
    private var overlay: TrackingOverlayView? = null
    private var pipeline: VisionPipeline? = null
    private var remoteMessenger: Messenger? = null
    private var serviceBound = false
    private var running = false
    private var token = ""
    private var includeHandsFaces = true
    private var deliveredFrames = 0
    private var lastNotificationAt = 0L

    private val visorConnection = object : ServiceConnection {
        override fun onServiceConnected(component: ComponentName?, binder: IBinder?) {
            if (!running || binder == null) {
                report("Visor camera service returned no connection")
                return
            }
            remoteMessenger = Messenger(binder)
            try {
                remoteMessenger?.send(Message.obtain(null, MSG_SUBSCRIBE).apply {
                    data = Bundle().apply {
                        putString("token", token)
                        putLong("interval_ms", 200L) // The documented 5 FPS maximum.
                    }
                    replyTo = frameMessenger
                })
                report("Connected to Visor; waiting for Live View frames")
            } catch (error: RemoteException) {
                report("Visor subscription failed: " + (error.message ?: "disconnected"))
            }
        }

        override fun onServiceDisconnected(component: ComponentName?) {
            remoteMessenger = null
            overlay?.clear()
            report("Visor disconnected; waiting for it to reconnect")
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (running) return START_NOT_STICKY

        token = intent?.getStringExtra(EXTRA_TOKEN)?.trim().orEmpty()
        includeHandsFaces = intent?.getBooleanExtra(EXTRA_ALL_DETECTORS, true) ?: true
        if (token.isEmpty() || !Settings.canDrawOverlays(this)) {
            report("Grant overlay permission and provide a Visor token first")
            stopSelf()
            return START_NOT_STICKY
        }

        createChannel()
        startForeground(NOTIFICATION_ID, notification("Preparing tracker overlay"))
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
                // Android 12+ permits touch-through on this explicitly translucent
                // non-touchable overlay below the system obscuring-opacity limit.
                alpha = 0.75f
            }
            windows.addView(view, params)
            overlay = view
        } catch (error: Exception) {
            report("Overlay permission/window failed: " + (error.message ?: "unknown"))
            stopSelf()
            return START_NOT_STICKY
        }
        running = true
        active = true
        report("Loading local detection models")
        worker.execute {
            try {
                val created = VisionPipeline(this, includeHandsFaces)
                mainHandler.post {
                    if (!running) {
                        worker.execute { created.close() }
                        return@post
                    }
                    pipeline = created
                    try {
                        serviceBound = bindService(
                            Intent(VISOR_FRAMES_ACTION).setPackage(VISOR_PACKAGE),
                            visorConnection, Context.BIND_AUTO_CREATE
                        )
                        if (!serviceBound) report("Visor rejected frame service; enable Developer Mode")
                    } catch (error: Exception) {
                        report("Visor frame bind failed: " + (error.message ?: "unknown"))
                    }
                }
            } catch (error: Exception) {
                mainHandler.post { report("MediaPipe initialization failed: " + (error.message ?: "unknown")) }
            }
        }
        return START_NOT_STICKY
    }

    private fun onFrameMessage(message: Message) {
        if (!running) return
        when (message.what) {
            MSG_FRAME -> {
                val jpeg = try { message.data.getByteArray("jpeg") } catch (_: Exception) { null } ?: return
                if (!processing.compareAndSet(false, true)) return // Never queue stale video.
                worker.execute {
                    var bitmap: Bitmap? = null
                    try {
                        bitmap = BitmapFactory.decodeByteArray(
                            jpeg, 0, jpeg.size, BitmapFactory.Options().apply {
                                inPreferredConfig = Bitmap.Config.ARGB_8888
                            }
                        )
                        if (bitmap == null) throw IllegalStateException("Visor JPEG could not decode")
                        val detected = pipeline?.detect(bitmap) ?: return@execute
                        mainHandler.post {
                            if (!running) return@post
                            overlay?.updateFrame(detected)
                            deliveredFrames++
                            val now = SystemClock.elapsedRealtime()
                            if (now - lastNotificationAt > 5000L) {
                                lastNotificationAt = now
                                report("Tracking over Visor: " + deliveredFrames + " frames, " +
                                    detected.boxes.size + " boxes")
                            }
                        }
                    } catch (error: Exception) {
                        mainHandler.post { report("Detection error: " + (error.message ?: "unknown")) }
                    } finally {
                        bitmap?.recycle()
                        processing.set(false)
                    }
                }
            }
            MSG_REFUSED -> {
                val reason = try { message.data.getString("reason") } catch (_: Exception) { null }
                overlay?.clear()
                report("Visor refused frames (" + (reason ?: "unknown") + "); check Developer token")
            }
        }
    }

    private fun report(message: String) {
        lastStatus = message
        if (running) {
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .notify(NOTIFICATION_ID, notification(message))
        }
    }

    private fun createChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(
            CHANNEL_ID, "Visor tracking overlay", NotificationManager.IMPORTANCE_LOW
        ))
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
            .setContentTitle("Visor Tracker — overlay active")
            .setContentText(line)
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop overlay", stop)
            .build()
    }

    override fun onDestroy() {
        running = false
        active = false
        try {
            remoteMessenger?.send(Message.obtain(null, MSG_UNSUBSCRIBE).apply { replyTo = frameMessenger })
        } catch (_: Exception) { }
        if (serviceBound) {
            try { unbindService(visorConnection) } catch (_: Exception) { }
            serviceBound = false
        }
        overlay?.let { view ->
            view.clear()
            try { windows.removeViewImmediate(view) } catch (_: Exception) { }
        }
        overlay = null
        val old = pipeline
        pipeline = null
        if (old != null) worker.execute { old.close() }
        worker.shutdown()
        token = ""
        lastStatus = "Overlay stopped"
        super.onDestroy()
    }

    companion object {
        const val ACTION_START = "com.mattumali.visortracker.START_OVERLAY"
        const val ACTION_STOP = "com.mattumali.visortracker.STOP_OVERLAY"
        const val EXTRA_TOKEN = "token"
        const val EXTRA_ALL_DETECTORS = "all_detectors"
        private const val CHANNEL_ID = "visor_tracker_overlay"
        private const val NOTIFICATION_ID = 4421
        private const val VISOR_PACKAGE = "com.macedonga.visor"
        private const val VISOR_FRAMES_ACTION = "com.macedonga.visor.action.LIVE_FRAMES"
        private const val MSG_SUBSCRIBE = 1
        private const val MSG_UNSUBSCRIBE = 2
        private const val MSG_FRAME = 3
        private const val MSG_REFUSED = 4
        @Volatile var active = false
            private set
        @Volatile var lastStatus = "Overlay not started"
            private set
    }
}
