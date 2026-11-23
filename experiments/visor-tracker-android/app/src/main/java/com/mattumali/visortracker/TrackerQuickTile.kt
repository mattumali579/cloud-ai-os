package com.mattumali.visortracker

import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import android.provider.Settings
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/**
 * User-added Quick Settings toggle. Swipe down while Visor Live View is open,
 * tap "Tracker" to start/stop boxes, without navigating to this app.
 *
 * Android handles the tile tap as an explicit user gesture. If Android blocks
 * foreground-service startup or permissions are missing, open setup rather
 * than pretending tracking started.
 */
class TrackerQuickTile : TileService() {
    override fun onStartListening() {
        super.onStartListening()
        updateState()
    }

    override fun onClick() {
        super.onClick()
        if (OverlayTrackingService.active) {
            try {
                startService(
                    Intent(this, OverlayTrackingService::class.java)
                        .setAction(OverlayTrackingService.ACTION_STOP)
                )
            } catch (_: Exception) { /* Service may have already exited. */ }
            updateState()
            return
        }

        if (!Settings.canDrawOverlays(this)) {
            openSetup()
            return
        }

        val prefs = getSharedPreferences(OverlayTrackingService.PREFS, MODE_PRIVATE)
        try {
            startForegroundService(
                Intent(this, OverlayTrackingService::class.java)
                    .setAction(OverlayTrackingService.ACTION_START)
                    .putExtra(
                        OverlayTrackingService.EXTRA_ALL_DETECTORS,
                        prefs.getBoolean(OverlayTrackingService.PREF_HANDS_FACES, false)
                    )
                    .putExtra(
                        OverlayTrackingService.EXTRA_PRESET,
                        prefs.getString(OverlayTrackingService.PREF_PRESET, DetectionPreset.YOLO26_SEG.name)
                    )
                    .putExtra(
                        OverlayTrackingService.EXTRA_ENABLE_DEPTH,
                        prefs.getBoolean(OverlayTrackingService.PREF_ENABLE_DEPTH, true)
                    )
                    .putExtra(
                        OverlayTrackingService.EXTRA_DEPTH_BACKEND,
                        prefs.getString(OverlayTrackingService.PREF_DEPTH_BACKEND, DepthEstimator.DepthBackend.YOLO26_DEPTH.name)
                    )
                    .putExtra(
                        OverlayTrackingService.EXTRA_STRICT_3D,
                        prefs.getBoolean(OverlayTrackingService.PREF_STRICT_3D, true)
                    )
            )
            updateState()
        } catch (_: Exception) {
            openSetup()
        }
    }

    private fun updateState() {
        qsTile?.let {
            it.state = if (OverlayTrackingService.active) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
            it.label = "Visor Tracker"
            it.updateTile()
        }
    }

    private fun openSetup() {
        val launch = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        if (Build.VERSION.SDK_INT >= 34) {
            val pending = PendingIntent.getActivity(
                this, 11, launch,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            startActivityAndCollapse(pending)
        } else {
            @Suppress("DEPRECATION")
            startActivityAndCollapse(launch)
        }
    }
}
