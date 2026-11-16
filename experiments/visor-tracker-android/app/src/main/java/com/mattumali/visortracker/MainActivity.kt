package com.mattumali.visortracker

import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.graphics.BitmapFactory
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Message
import android.os.Messenger
import android.os.RemoteException
import android.text.InputType
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class MainActivity : Activity() {
    private val executor = Executors.newSingleThreadExecutor()
    private val processing = AtomicBoolean(false)
    private lateinit var tokenInput: EditText
    private lateinit var status: TextView
    private lateinit var preview: ImageView
    private lateinit var startButton: Button
    private var pipeline: VisionPipeline? = null
    private var serviceMessenger: Messenger? = null
    private var bound = false
    private var token = ""

    private val frameMessenger = Messenger(Handler(Looper.getMainLooper()) { message ->
        handleFrameMessage(message)
        true
    })

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            if (binder == null) {
                showStatus("Visor camera service returned no binder.")
                return
            }
            serviceMessenger = Messenger(binder)
            val subscribe = Message.obtain(null, MSG_SUBSCRIBE).apply {
                data = Bundle().apply {
                    putString("token", token)
                    putLong("interval_ms", FRAME_INTERVAL_MS)
                }
                replyTo = frameMessenger
            }
            try {
                serviceMessenger?.send(subscribe)
                showStatus("Connected to Visor. Start Live View to receive frames.")
            } catch (error: RemoteException) {
                showStatus("Could not subscribe to Visor frames: ${error.message}")
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            serviceMessenger = null
            showStatus("Visor disconnected.")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        buildUi()
    }

    private fun buildUi() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(18), dp(16), dp(12))
        }
        val scroller = ScrollView(this)
        val content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        content.addView(TextView(this).apply {
            text = "Visor Live Tracker"
            textSize = 24f
        })
        content.addView(TextView(this).apply {
            text = "On-device MediaPipe hand, face, and COCO object detection. Open Visor Live View, then start tracking here."
            textSize = 14f
            setPadding(0, dp(8), 0, dp(10))
        })
        tokenInput = EditText(this).apply {
            hint = "Paste Visor Developer Mode token"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            maxLines = 1
        }
        content.addView(tokenInput)
        startButton = Button(this).apply {
            text = "Start tracking"
            setOnClickListener { startTracking() }
        }
        content.addView(startButton)
        content.addView(Button(this).apply {
            text = "Stop tracking"
            setOnClickListener { stopTracking("Stopped.") }
        })
        status = TextView(this).apply {
            text = "Enable Developer mode in Visor → Settings → Advanced. Keep the token private."
            textSize = 14f
            setPadding(0, dp(8), 0, dp(8))
        }
        content.addView(status)
        preview = ImageView(this).apply {
            adjustViewBounds = true
            scaleType = ImageView.ScaleType.FIT_CENTER
            contentDescription = "Live View with hand, face, and object tracking overlays"
        }
        content.addView(preview, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, dp(420)
        ))
        root.addView(content)
        scroller.addView(root)
        setContentView(scroller)
    }

    private fun startTracking() {
        if (bound || pipeline != null) return
        token = tokenInput.text.toString().trim()
        if (token.isBlank()) {
            showStatus("Paste the token shown under Visor Developer Mode first.")
            return
        }
        if (!packageManager.getApplicationInfoOrNull(VISOR_PACKAGE)) {
            showStatus("Visor is not installed or Android cannot see it. Install Visor and retry.")
            return
        }
        tokenInput.visibility = View.GONE
        startButton.isEnabled = false
        showStatus("Loading on-device vision models…")
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        executor.execute {
            try {
                pipeline = VisionPipeline(this)
                runOnUiThread {
                    val intent = Intent(ACTION_LIVE_FRAMES).setPackage(VISOR_PACKAGE)
                    bound = bindService(intent, serviceConnection, Context.BIND_AUTO_CREATE)
                    if (bound) {
                        showStatus("Models ready. Bound to Visor; now start Live View.")
                    } else {
                        pipeline?.close()
                        pipeline = null
                        showStatus("Visor refused the LIVE_FRAMES service bind. Check Developer Mode and app access.")
                        resetStartUi()
                    }
                }
            } catch (error: Exception) {
                runOnUiThread {
                    showStatus("Could not start vision pipeline: ${error.message}")
                    pipeline?.close()
                    pipeline = null
                    resetStartUi()
                }
            }
        }
    }

    private fun handleFrameMessage(message: Message) {
        when (message.what) {
            MSG_FRAME -> {
                val bytes = message.data?.getByteArray("jpeg") ?: return
                if (!processing.compareAndSet(false, true)) return
                executor.execute {
                    try {
                        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                        if (bitmap == null) {
                            runOnUiThread { showStatus("Visor sent an unreadable JPEG frame.") }
                        } else {
                            val result = pipeline?.analyze(bitmap)
                            bitmap.recycle()
                            if (result != null) runOnUiThread {
                                preview.setImageBitmap(result)
                                showStatus("Tracking live — Visor frame time ${message.data.getLong("time")} · ${result.width}×${result.height}")
                            }
                        }
                    } catch (error: Exception) {
                        runOnUiThread { showStatus("Frame processing error: ${error.message}") }
                    } finally {
                        processing.set(false)
                    }
                }
            }
            MSG_REFUSED -> {
                val reason = message.data?.getString("reason") ?: "unknown"
                showStatus("Visor refused camera frames ($reason). Check token and Developer Mode.")
            }
        }
    }

    private fun stopTracking(message: String) {
        try {
            serviceMessenger?.send(Message.obtain(null, MSG_UNSUBSCRIBE).apply { replyTo = frameMessenger })
        } catch (_: RemoteException) {
        }
        if (bound) {
            unbindService(serviceConnection)
            bound = false
        }
        serviceMessenger = null
        pipeline?.close()
        pipeline = null
        token = ""
        tokenInput.visibility = View.VISIBLE
        resetStartUi()
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        showStatus(message)
    }

    private fun resetStartUi() {
        startButton.isEnabled = true
        tokenInput.visibility = View.VISIBLE
    }

    private fun showStatus(message: String) {
        if (::status.isInitialized) status.text = message
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    // Switching to Visor to start Live View must not cancel the frame subscription.
    // Android calls onStop() when the Tracker is no longer visible.
    override fun onStop() {
        super.onStop()
    }

    override fun onDestroy() {
        if (bound || pipeline != null) stopTracking("Tracker closed.")
        executor.shutdownNow()
        super.onDestroy()
    }

    companion object {
        private const val VISOR_PACKAGE = "com.macedonga.visor"
        private const val ACTION_LIVE_FRAMES = "com.macedonga.visor.action.LIVE_FRAMES"
        private const val MSG_SUBSCRIBE = 1
        private const val MSG_UNSUBSCRIBE = 2
        private const val MSG_FRAME = 3
        private const val MSG_REFUSED = 4
        private const val FRAME_INTERVAL_MS = 250L
    }
}

@Suppress("DEPRECATION")
private fun android.content.pm.PackageManager.getApplicationInfoOrNull(packageName: String): Boolean =
    try {
        getApplicationInfo(packageName, 0)
        true
    } catch (_: android.content.pm.PackageManager.NameNotFoundException) {
        false
    }
