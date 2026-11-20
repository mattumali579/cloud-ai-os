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
        accurate = CheckBox(this).apply {
            text = "Higher accuracy (EfficientDet-Lite2; more battery / lower AI FPS)"
            isChecked = DetectionPreset.read(
                prefs.getString(OverlayTrackingService.PREF_PRESET, null)
            ) == DetectionPreset.ACCURATE
            setOnCheckedChangeListener { _, checked ->
                prefs.edit().putString(
                    OverlayTrackingService.PREF_PRESET,
                    if (checked) DetectionPreset.ACCURATE.name else DetectionPreset.FAST.name
                ).apply()
            }
        }
        root.addView(accurate)
        root.addView(TextView(this).apply {
            text = "One object. An extended index finger locks the object on that 2D ray. " +
                "The ray is on the camera image and cannot measure depth. " +
                "The lock stays after the hand drops, and follows the object until it leaves. " +
                "A closed fist or a fully curled index does not switch it. Holding that gesture " +
                "releases the lock and does not send a command. If the locked object leaves and " +
                "another object was already visible, the next detection can take it without a " +
                "second model pass. With no hand, the center of the picture still locks. " +
                "Motion does not swap the lock every frame. Fast mode stays " +
                "EfficientDet-Lite0. An unsure name is shown as object."
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
        root.addView(CheckBox(this).apply {
            text = "Show the Live View proof mark"
            isChecked = prefs.getBoolean(OverlayTrackingService.PREF_PROBE, true)
            setOnCheckedChangeListener { _, checked ->
                prefs.edit().putBoolean(OverlayTrackingService.PREF_PROBE, checked).apply()
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
                .putExtra(OverlayTrackingService.EXTRA_PRESET,
                    if (accurate.isChecked) DetectionPreset.ACCURATE.name else DetectionPreset.FAST.name))
            status.text = "Starting fast overlay; open Visor Live View"
        } catch (error: Exception) {
            status.text = "Could not start tracking: " + error.message
        }
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}
