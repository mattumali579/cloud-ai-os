package com.mattumali.visortracker

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * Launcher is only a permission/token/stop control. Live tracking happens in the
 * foreground OverlayTrackingService while the user watches Visor's own Live View.
 */
class MainActivity : Activity() {
    private lateinit var tokenField: EditText
    private lateinit var includeAll: CheckBox
    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val pad = dp(20)
            setPadding(pad, pad, pad, pad)
        }
        val scroller = ScrollView(this)
        scroller.addView(root)

        root.addView(TextView(this).apply {
            text = "Visor Tracker Overlay"
            textSize = 24f
        })
        root.addView(TextView(this).apply {
            text = "Draw live detection boxes ON TOP of Visor Live View (phone screen). " +
                "No separate camera preview. Leave Visor in the expanded, full-screen view."
            textSize = 15f
            setPadding(0, dp(12), 0, dp(14))
        })
        root.addView(Button(this).apply {
            text = "1. Allow display over other apps"
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + packageName)))
            }
        })
        tokenField = EditText(this).apply {
            hint = "Paste your Visor Developer Mode token"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setSingleLine(true)
            setText(getSharedPreferences("tracker", MODE_PRIVATE).getString("token", ""))
        }
        root.addView(tokenField)
        includeAll = CheckBox(this).apply {
            text = "Also detect hands and faces (slower)"
            isChecked = true
        }
        root.addView(includeAll)
        root.addView(Button(this).apply {
            text = "2. START LIVE VIEW OVERLAY"
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
            text = "After START: open Visor Live View and keep it expanded. " +
                "Labels appear over Visor's live camera, not in the glasses. " +
                "Use the notification's Stop overlay action to end tracking. " +
                "Native video keeps its own FPS; detection updates are limited by " +
                "Visor's current JPEG service to at most 5 FPS."
            textSize = 13f
        })
        setContentView(scroller)
    }

    override fun onResume() {
        super.onResume()
        if (::status.isInitialized) {
            status.text = if (OverlayTrackingService.active) {
                OverlayTrackingService.lastStatus
            } else if (!Settings.canDrawOverlays(this)) {
                "Overlay permission not granted"
            } else {
                "Overlay permission granted. Ready to start."
            }
        }
    }

    private fun startOverlay() {
        if (!Settings.canDrawOverlays(this)) {
            status.text = "Enable Display over other apps first."
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:" + packageName)))
            return
        }
        val token = tokenField.text.toString().trim()
        if (token.isBlank()) {
            status.text = "Copy the token from Visor Settings > Advanced > Developer Mode."
            return
        }
        getSharedPreferences("tracker", MODE_PRIVATE)
            .edit().putString("token", token).apply()
        try {
            startForegroundService(Intent(this, OverlayTrackingService::class.java)
                .setAction(OverlayTrackingService.ACTION_START)
                .putExtra(OverlayTrackingService.EXTRA_TOKEN, token)
                .putExtra(OverlayTrackingService.EXTRA_ALL_DETECTORS, includeAll.isChecked))
            status.text = "Starting overlay. Open Visor Live View; keep full-screen."
        } catch (error: Exception) {
            status.text = "Could not start overlay: " + (error.message ?: "unknown")
        }
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}
