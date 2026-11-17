package com.mattumali.visortracker

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
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
        root.addView(TextView(this).apply {
            text = "Visor Fast Tracking"
            textSize = 24f
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
            text = "Also detect hands and faces (slower)"
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
        root.addView(CheckBox(this).apply {
            text = "Automatic signed updates (Wi-Fi, no USB or manual APK downloads)"
            isChecked = TrackerUpdater.enabled(this@MainActivity)
            setOnCheckedChangeListener { _, enabled ->
                TrackerUpdater.configure(this@MainActivity, enabled)
                if (enabled && android.os.Build.VERSION.SDK_INT >= 26 &&
                    !packageManager.canRequestPackageInstalls()) {
                    startActivity(Intent(
                        Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:" + packageName)
                    ))
                }
            }
        })
        root.addView(Button(this).apply {
            text = "CHECK FOR UPDATES NOW"
            setOnClickListener {
                status.text = "Checking signed GitHub releases..."
                TrackerUpdater.checkInBackground(this@MainActivity, allowMetered = true) { message ->
                    runOnUiThread { if (::status.isInitialized) status.text = message }
                }
            }
        })
        root.addView(Button(this).apply {
            text = "3. START FAST OVERLAY"
            setOnClickListener { startOverlay() }
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
            text = "Automatic updates use the same signed app, with Android approval if required. " +
                "For fewer app switches: add the Visor Tracker Quick Settings tile " +
                "(swipe down twice > Edit > add Visor Tracker). Open Live View first, " +
                "then toggle tracking from Quick Settings. " +
                "The selected accuracy mode takes effect on the next start. " +
                "After START, switch to Visor and keep Live View fully expanded. " +
                "The system notification shows measured VIDEO FPS and DETECTION FPS. " +
                "For best speed, leave hands/faces unchecked. " +
                "Tracking is on your PHONE; ordinary Ray-Ban Meta glasses have no display. " +
                "Tap Stop in the ongoing notification when finished."
            textSize = 13f
        })
        setContentView(scroll)
        TrackerUpdater.schedule(this)
        if (TrackerUpdater.enabled(this)) {
            TrackerUpdater.checkInBackground(this) { message ->
                runOnUiThread {
                    if (::status.isInitialized) status.text = message
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        if (::status.isInitialized) {
            status.text = if (OverlayTrackingService.active) OverlayTrackingService.lastStatus
            else if (!Settings.canDrawOverlays(this)) "Overlay permission not granted"
            else "Overlay permission granted. Grant Visor camera access, then Start."
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
