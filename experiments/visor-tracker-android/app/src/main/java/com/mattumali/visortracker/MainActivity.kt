package com.mattumali.visortracker

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.core.content.FileProvider
import java.io.File
import android.text.InputType
import android.widget.EditText
import android.widget.Button
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import com.macedonga.visor.kit.RequestScopes
import com.macedonga.visor.kit.Visor
import com.macedonga.visor.kit.protocol.Scope

/** Launcher for the VDK camera grant and background overlay. No developer token needed. */
class MainActivity : ComponentActivity() {
    private lateinit var includeAll: CheckBox
    private lateinit var accurate: CheckBox
    private lateinit var status: TextView
    private lateinit var installUpdate: Button
    private var currentPreset: DetectionPreset = DetectionPreset.YOLO26_SEG
    private var currentDepthEnabled: Boolean = false
    private var currentDepthBackend: DepthEstimator.DepthBackend = DepthEstimator.DepthBackend.YOLO26_DEPTH
    private lateinit var detectorButton: Button
    private lateinit var depthButton: Button

    private val grantCamera = registerForActivityResult(RequestScopes()) { grants ->
        status.text = if (Scope.CAMERA in grants) {
            "Visor camera access approved. Tap START OVERLAY."
        } else {
            "Camera access not granted. Visor needs Camera permission to send video."
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = getSharedPreferences(OverlayTrackingService.PREFS, MODE_PRIVATE)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val pad = dp(20)
            setPadding(pad, pad, pad, pad)
        }
        val scroll = ScrollView(this).apply { addView(root) }
        val identity = installedIdentity()
        root.addView(TextView(this).apply {
            text = "Visor Fast Tracking ${identity.versionName}"
            textSize = 24f
        })
        root.addView(TextView(this).apply {
            text = identity.line + "\nIf this screen does not show ${identity.versionName} and " +
                "com.mattumali.visortracker.stable, this is the old Tracker. " +
                "The old 0.1.0 app has no Check for updates button. Leave it installed."
            textSize = 15f
            setPadding(0, dp(8), 0, dp(8))
        })
        root.addView(TextView(this).apply {
            text = "Uses Visor's VDK camera.live hardware video stream " +
                "(requested at 30 FPS) to place detection boxes over Visor's full-screen Live View. " +
                "Requires Visor Camera access; the Developer Mode token is NOT needed."
            textSize = 15f
            setPadding(0, dp(12), 0, dp(12))
        })
        root.addView(Button(this).apply {
            text = "1. Allow display over other apps"
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + packageName)))
            }
        })
        root.addView(Button(this).apply {
            text = "2. Grant Visor camera access"
            setOnClickListener {
                if (!Visor.isInstalled(this@MainActivity)) {
                    status.text = "Visor app is missing"
                } else {
                    try { grantCamera.launch(setOf(Scope.CAMERA)) }
                    catch (error: Exception) {
                        status.text = "Visor consent screen failed: " + error.message
                    }
                }
            }
        })
        includeAll = CheckBox(this).apply {
            text = "Also detect faces (slower). The pointing hand stays on."
            isChecked = prefs.getBoolean(OverlayTrackingService.PREF_HANDS_FACES, false)
            setOnCheckedChangeListener { _, checked ->
                prefs.edit().putBoolean(OverlayTrackingService.PREF_HANDS_FACES, checked).apply()
            }
        }
        root.addView(includeAll)
        val savedPreset = prefs.getString(OverlayTrackingService.PREF_PRESET, DetectionPreset.YOLO26_SEG.name)
        currentPreset = when (savedPreset) {
            DetectionPreset.ACCURATE.name -> DetectionPreset.ACCURATE
            DetectionPreset.FAST.name -> DetectionPreset.FAST
            else -> DetectionPreset.YOLO26_SEG
        }
        currentDepthEnabled = prefs.getBoolean(OverlayTrackingService.PREF_ENABLE_DEPTH, true)
        val savedDepth = prefs.getString(OverlayTrackingService.PREF_DEPTH_BACKEND, DepthEstimator.DepthBackend.YOLO26_DEPTH.name)
        currentDepthBackend = try {
            DepthEstimator.DepthBackend.valueOf(savedDepth ?: DepthEstimator.DepthBackend.YOLO26_DEPTH.name)
        } catch (_: Exception) {
            DepthEstimator.DepthBackend.YOLO26_DEPTH
        }

        detectorButton = Button(this).apply {
            fun updateText() {
                text = when (currentPreset) {
                    DetectionPreset.YOLO26_SEG -> "AI Detector: YOLO26n-seg (ONNX Seg & Box) [ACTIVE]"
                    DetectionPreset.FAST -> "AI Detector: EfficientDet-Lite0 (Fast Box)"
                    DetectionPreset.ACCURATE -> "AI Detector: EfficientDet-Lite2 (Accurate Box)"
                }
            }
            updateText()
            setOnClickListener {
                currentPreset = when (currentPreset) {
                    DetectionPreset.YOLO26_SEG -> DetectionPreset.FAST
                    DetectionPreset.FAST -> DetectionPreset.ACCURATE
                    DetectionPreset.ACCURATE -> DetectionPreset.YOLO26_SEG
                }
                updateText()
                accurate.isChecked = (currentPreset == DetectionPreset.ACCURATE)
                prefs.edit().putString(OverlayTrackingService.PREF_PRESET, currentPreset.name).apply()
            }
        }
        root.addView(detectorButton)

        depthButton = Button(this).apply {
            fun updateText() {
                text = if (!currentDepthEnabled) {
                    "Depth Backend: OFF [Tap to enable]"
                } else when (currentDepthBackend) {
                    DepthEstimator.DepthBackend.YOLO26_DEPTH -> "Depth Backend: YOLO26-Depth (Metric 3.0s) [ACTIVE]"
                    DepthEstimator.DepthBackend.DEPTH_ANYTHING_V2 -> "Depth Backend: Depth Anything V2 Small (Dense 24s)"
                }
            }
            updateText()
            setOnClickListener {
                if (!currentDepthEnabled) {
                    currentDepthEnabled = true
                    currentDepthBackend = DepthEstimator.DepthBackend.YOLO26_DEPTH
                } else if (currentDepthBackend == DepthEstimator.DepthBackend.YOLO26_DEPTH) {
                    currentDepthBackend = DepthEstimator.DepthBackend.DEPTH_ANYTHING_V2
                } else {
                    currentDepthEnabled = false
                }
                updateText()
                prefs.edit()
                    .putBoolean(OverlayTrackingService.PREF_ENABLE_DEPTH, currentDepthEnabled)
                    .putString(OverlayTrackingService.PREF_DEPTH_BACKEND, currentDepthBackend.name)
                    .apply()
            }
        }
        root.addView(depthButton)

        accurate = CheckBox(this).apply {
            text = "Legacy toggle: EfficientDet-Lite2 (Higher battery)"
            isChecked = (currentPreset == DetectionPreset.ACCURATE)
            setOnCheckedChangeListener { _, checked ->
                currentPreset = if (checked) DetectionPreset.ACCURATE else DetectionPreset.FAST
                detectorButton.text = if (checked) "AI Detector: EfficientDet-Lite2 (Accurate Box)" else "AI Detector: EfficientDet-Lite0 (Fast Box)"
                prefs.edit().putString(OverlayTrackingService.PREF_PRESET, currentPreset.name).apply()
            }
        }
        root.addView(accurate)
        root.addView(TextView(this).apply {
            text = "An intentional extended-index point projects a ray in the camera image. " +
                "A target locks only after repeated ray intersections with the same valid object. " +
                "A short landmark or detector dropout is tolerated; the lock releases when pointing " +
                "stops or the ray remains off target. Open palms, fists, arms without 21 landmarks, " +
                "and no-hand frames cannot create a lock. When cloud grounding is explicitly enabled, " +
                "one frame may be sent to Gemini after a sustained point has no reliable local target. " +
                "Gemini can propose visible boxes, but the same ray and multi-frame confirmation still " +
                "decide the lock."
            textSize = 14f
            setPadding(0, dp(10), 0, dp(4))
        })
        root.addView(TextView(this).apply {
            text = "ONE OBJECT DISTANCE: measure the object from the glasses, enter that " +
                "distance in feet, aim so the object is centered and fully visible, then " +
                "tap Calibrate. The notification Calibrate action works during Live View " +
                "and confirms the reference feet. Only that track then shows approximate " +
                "feet, D = D0 × (h0 / h), as its apparent height changes. Same object, " +
                "roughly the same orientation. Tilt, rotation, and occlusion can miss by " +
                "more than 25%. There is no depth sensor. If that track disappears, the " +
                "overlay says distance unavailable instead of moving the feet to another object."
            textSize = 14f
            setPadding(0, dp(10), 0, dp(4))
        })
        val referenceDistance = EditText(this).apply {
            hint = "Starting distance in feet (e.g. 6)"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            setSingleLine(true)
            setText(prefs.getString("reference_distance_feet", "6"))
        }
        root.addView(referenceDistance)
        root.addView(Button(this).apply {
            text = "CALIBRATE CENTER OBJECT"
            setOnClickListener {
                val measured = referenceDistance.text.toString().trim().toFloatOrNull()
                if (measured == null || !measured.isFinite() || measured !in 0.5f..150f) {
                    status.text = "Enter an actual measured distance from 0.5 to 150 ft"
                } else if (!OverlayTrackingService.active) {
                    status.text = "Start tracking and open Live View first"
                } else {
                    prefs.edit().putString("reference_distance_feet", measured.toString()).apply()
                    startService(Intent(this@MainActivity, OverlayTrackingService::class.java)
                        .setAction(OverlayTrackingService.ACTION_CALIBRATE)
                        .putExtra(OverlayTrackingService.EXTRA_REFERENCE_FEET, measured))
                    status.text = "Calibrating the detected object near camera center..."
                    status.postDelayed({
                        if (::status.isInitialized) status.text = OverlayTrackingService.lastStatus
                    }, 250L)
                }
            }
        })
        root.addView(Button(this).apply {
            text = "CLEAR DISTANCE"
            setOnClickListener {
                if (!OverlayTrackingService.active) {
                    status.text = "Distance measurement not running"
                } else {
                    startService(Intent(this@MainActivity, OverlayTrackingService::class.java)
                        .setAction(OverlayTrackingService.ACTION_CLEAR_DISTANCE))
                    status.text = "Distance calibration cleared"
                }
            }
        })
        root.addView(CheckBox(this).apply {
            text = "Tell me when a signed update is published"
            isChecked = TrackerUpdater.enabled(this@MainActivity)
            setOnCheckedChangeListener { _, enabled ->
                TrackerUpdater.configure(this@MainActivity, enabled)
            }
        })
        installUpdate = Button(this).apply {
            text = "UPDATE THIS APP"
            setOnClickListener { updateThisApp() }
        }
        root.addView(installUpdate)
        root.addView(Button(this).apply {
            text = "OPEN RELEASE PAGE"
            setOnClickListener {
                startActivity(
                    Intent(Intent.ACTION_VIEW, Uri.parse(TrackerRelease.RELEASES_PAGE))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
                status.text = "Download Visor-Fast-Tracking.apk. Tap Update. Do not uninstall."
            }
        })
        root.addView(CheckBox(this).apply {
            text = "Show the Live View proof mark"
            isChecked = prefs.getBoolean(OverlayTrackingService.PREF_PROBE, true)
            setOnCheckedChangeListener { _, checked ->
                prefs.edit().putBoolean(OverlayTrackingService.PREF_PROBE, checked).apply()
            }
        })
        root.addView(CheckBox(this).apply {
            text = "Opt-in: Capture raw pre-overlay frames locally (no cloud upload)"
            isChecked = prefs.getBoolean(OverlayTrackingService.PREF_PRIVATE_CAPTURE, false)
            setOnCheckedChangeListener { _, checked ->
                prefs.edit().putBoolean(OverlayTrackingService.PREF_PRIVATE_CAPTURE, checked).apply()
            }
        })
        root.addView(CheckBox(this).apply {
            text = "Opt-in: Send a camera frame to Gemini only after a stable point has no local target"
            isChecked = prefs.getBoolean(OverlayTrackingService.PREF_CLOUD_GROUNDING, false)
            setOnCheckedChangeListener { _, checked ->
                prefs.edit().putBoolean(OverlayTrackingService.PREF_CLOUD_GROUNDING, checked).apply()
            }
        })
        root.addView(Button(this).apply {
            text = "CLEAR PRIVATE CAPTURES"
            setOnClickListener {
                startService(Intent(this@MainActivity, OverlayTrackingService::class.java)
                    .setAction(OverlayTrackingService.ACTION_CLEAR_PRIVATE_CAPTURES))
                status.text = "Cleared private captures"
            }
        })
        root.addView(Button(this).apply {
            text = "3. START FAST OVERLAY"
            setOnClickListener { startOverlay() }
        })
        root.addView(Button(this).apply {
            text = "EXPORT DIAGNOSTICS"
            setOnClickListener { exportDiagnostics() }
        })
        root.addView(Button(this).apply {
            text = "STOP OVERLAY"
            setOnClickListener {
                startService(Intent(this@MainActivity, OverlayTrackingService::class.java)
                    .setAction(OverlayTrackingService.ACTION_STOP))
                status.text = "Stop requested"
            }
        })
        status = TextView(this).apply {
            textSize = 15f
            setPadding(0, dp(14), 0, dp(12))
        }
        root.addView(status)
        root.addView(TextView(this).apply {
            text = "Updates open the signed GitHub release. The app does not install packages by itself. " +
                "For fewer app switches: add the Visor Tracker Quick Settings tile " +
                "(swipe down twice > Edit > add Visor Tracker). Open Live View first, " +
                "then toggle tracking from Quick Settings. " +
                "The selected accuracy mode takes effect on the next start. " +
                "After START, switch to Visor and keep Live View fully expanded. " +
                "A small green corner mark reading this version is the proof that Android " +
                "is drawing over Live View, before any object box exists. " +
                "Its second line is the first failed hop: OVERLAY BLOCKED, " +
                "NO CAMERA FRAMES, or 0 OBJECTS DETECTED. " +
                "The same line is in the ongoing notification. " +
                "EXPORT DIAGNOSTICS writes that history on this phone. " +
                "It stays there when Live View switches Wi-Fi. USB debugging is not required. " +
                "Turn the mark off with the checkbox above when you no longer need it. " +
                "The notification shows decoded video FPS, detector FPS, motion-tracker FPS, " +
                "changed-box FPS, draw FPS, tracker interval and box age as p50/p95/p99 milliseconds, " +
                "dropped frames, and median milliseconds for grayscale, " +
                "YUV conversion, inference, motion, and detection age. " +
                "Changed-box FPS does not count a redraw of the same rectangle. " +
                "For best speed, leave hands/faces unchecked. " +
                "Tracking is on your PHONE; ordinary Ray-Ban Meta glasses have no display. " +
                "For distance calibration without leaving Live View, first set the " +
                "starting distance above, then use the ongoing notification's " +
                "Calibrate action while aiming at the target. " +
                "Tap Stop in the ongoing notification when finished."
            textSize = 13f
        })
        setContentView(scroll)
        TrackerUpdater.schedule(this)
        if (TrackerUpdater.enabled(this)) {
            TrackerUpdater.checkInBackground(
                this,
                onStatus = { message ->
                    runOnUiThread { if (::status.isInitialized) status.text = message }
                },
                onOffer = { _ -> },
            )
        }
    }

    override fun onResume() {
        super.onResume()
        if (::status.isInitialized) {
            val known = OverlayTrackingService.lastStatus
            status.text = when {
                OverlayTrackingService.active -> known
                known != "Overlay not started" -> known
                !Settings.canDrawOverlays(this) ->
                    "OVERLAY BLOCKED | Display over other apps is denied"
                else -> "Overlay permission granted. Grant Visor camera access, then Start."
            }
        }
    }

    private data class InstallIdentity(val versionName: String, val line: String)

    @Suppress("DEPRECATION")
    private fun installedIdentity(): InstallIdentity {
        val info = packageManager.getPackageInfo(packageName, 0)
        val versionName = info.versionName ?: "?"
        val versionCode = if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else info.versionCode.toLong()
        return InstallIdentity(versionName, "$packageName  $versionName ($versionCode)")
    }

    private fun updateThisApp() {
        if (Build.VERSION.SDK_INT >= 26 && !packageManager.canRequestPackageInstalls()) {
            status.text = "Allow this app to install updates, then tap UPDATE THIS APP again."
            startActivity(Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:$packageName")
            ))
            return
        }
        status.text = "Checking for an update..."
        installUpdate.isEnabled = false
        TrackerUpdater.checkInBackground(
            this,
            allowMetered = true,
            onStatus = { message ->
                runOnUiThread { if (::status.isInitialized) status.text = message }
            },
            onOffer = { offer ->
                if (offer == null) {
                    runOnUiThread { installUpdate.isEnabled = true }
                    return@checkInBackground
                }
                try {
                    val apk = TrackerUpdater.downloadUpdate(applicationContext, offer)
                    runOnUiThread {
                        installUpdate.isEnabled = true
                        startActivity(TrackerUpdater.installIntent(this, apk))
                        status.text = "Tap Update. Do not uninstall."
                    }
                } catch (error: Exception) {
                    runOnUiThread {
                        installUpdate.isEnabled = true
                        status.text = "Update failed: " + (error.message ?: "unknown")
                    }
                }
            },
        )
    }

    private fun exportDiagnostics() {
        val text = TrackerLog.read(filesDir)
        if (text.isBlank()) {
            status.text = "No diagnostics yet. Start the overlay, open Live View, then export."
            return
        }
        val tail = text.takeLast(1200)
        status.text = tail
        val file = File(filesDir, TrackerLog.FILE_NAME)
        try {
            val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
            val share = Intent(Intent.ACTION_SEND)
                .setType("text/plain")
                .putExtra(Intent.EXTRA_STREAM, uri)
                .putExtra(Intent.EXTRA_SUBJECT, "Visor Tracker diagnostics")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            startActivity(Intent.createChooser(share, "Export Visor Tracker diagnostics"))
        } catch (error: Exception) {
            status.text = tail + "\nExport sheet failed: " + (error.message ?: "unknown")
        }
    }

    private fun startOverlay() {
        if (!Settings.canDrawOverlays(this)) {
            status.text = "Allow Display over other apps first"
            return
        }
        try {
            startForegroundService(Intent(this, OverlayTrackingService::class.java)
                .setAction(OverlayTrackingService.ACTION_START)
                .putExtra(OverlayTrackingService.EXTRA_ALL_DETECTORS, includeAll.isChecked)
                .putExtra(OverlayTrackingService.EXTRA_PRESET, currentPreset.name)
                .putExtra(OverlayTrackingService.EXTRA_ENABLE_DEPTH, currentDepthEnabled)
                .putExtra(OverlayTrackingService.EXTRA_DEPTH_BACKEND, currentDepthBackend.name))
            status.text = "Starting overlay with ${currentPreset.name}; open Visor Live View"
        } catch (error: Exception) {
            status.text = "Could not start tracking: " + error.message
        }
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}
