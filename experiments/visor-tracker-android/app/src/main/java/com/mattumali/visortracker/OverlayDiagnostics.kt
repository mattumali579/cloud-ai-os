package com.mattumali.visortracker

import java.util.Locale

/**
 * Names the first hop that explains an invisible overlay.
 * A later hop is not reported while an earlier one has already failed.
 */
enum class OverlayHop {
    OVERLAY_BLOCKED,
    SERVICE_STOPPED,
    CAMERA_DENIED,
    DETECTOR_FAILED,
    STREAM_FAILED,
    CONNECTING,
    NO_CAMERA_FRAMES,
    WAITING_FOR_DETECTOR,
    BOXES_HIDDEN,
    ZERO_OBJECTS,
    TRACKING,
}

data class OverlaySnapshot(
    val versionName: String,
    val versionCode: Int,
    val packageName: String,
    val overlayPermission: Boolean,
    val serviceRunning: Boolean,
    val overlayAttached: Boolean,
    val overlayError: String? = null,
    /** Null until Visor.connect has answered. */
    val cameraGranted: Boolean? = null,
    val liveConnected: Boolean = false,
    val framesReceived: Int = 0,
    val inferencesCompleted: Int = 0,
    val rawDetections: Int = 0,
    val freshBoxes: Int = 0,
    val drawnBoxes: Int = 0,
    val boxAgeMs: Int = -1,
    val decoderFps: Float = 0f,
    val aiFps: Float = 0f,
    val failure: String? = null,
    val probeEnabled: Boolean = true,
)

data class OverlayDiagnosis(val hop: OverlayHop, val code: String, val detail: String)

object OverlayDiagnostics {
    const val CODE_BLOCKED = "OVERLAY BLOCKED"
    const val CODE_NO_FRAMES = "NO CAMERA FRAMES"
    const val CODE_ZERO = "0 OBJECTS DETECTED"

    fun diagnose(snapshot: OverlaySnapshot): OverlayDiagnosis {
        val failure = snapshot.failure?.trim()?.takeIf { it.isNotEmpty() }
        val overlayError = snapshot.overlayError?.trim()?.takeIf { it.isNotEmpty() }
        if (!snapshot.overlayPermission || overlayError != null ||
            (snapshot.serviceRunning && !snapshot.overlayAttached)
        ) {
            val why = when {
                !snapshot.overlayPermission -> "Display over other apps is denied"
                overlayError != null -> overlayError
                else -> "The overlay window is not attached"
            }
            return OverlayDiagnosis(OverlayHop.OVERLAY_BLOCKED, CODE_BLOCKED, why)
        }
        if (!snapshot.serviceRunning) {
            return OverlayDiagnosis(
                OverlayHop.SERVICE_STOPPED,
                "SERVICE STOPPED",
                "Tracking service is not running",
            )
        }
        if (snapshot.cameraGranted == false) {
            return OverlayDiagnosis(
                OverlayHop.CAMERA_DENIED,
                "CAMERA DENIED",
                "Visor CAMERA scope is not granted",
            )
        }
        if (snapshot.freshBoxes > 0 || snapshot.drawnBoxes > 0) {
            val age = if (snapshot.boxAgeMs < 0) "age n/a" else "age ${snapshot.boxAgeMs} ms"
            return OverlayDiagnosis(
                OverlayHop.TRACKING,
                "TRACKING",
                "${snapshot.freshBoxes} boxes, $age",
            )
        }
        if (failure != null) {
            val detector = failure.startsWith("MediaPipe")
            return OverlayDiagnosis(
                if (detector) OverlayHop.DETECTOR_FAILED else OverlayHop.STREAM_FAILED,
                if (detector) "DETECTOR FAILED" else "STREAM FAILED",
                failure,
            )
        }
        if (snapshot.cameraGranted == null || !snapshot.liveConnected) {
            val detail = if (snapshot.cameraGranted == null) {
                "Waiting for Visor.connect"
            } else {
                "Visor CAMERA is granted; camera.live is not collecting"
            }
            return OverlayDiagnosis(OverlayHop.CONNECTING, "CONNECTING", detail)
        }
        if (snapshot.framesReceived <= 0) {
            return OverlayDiagnosis(
                OverlayHop.NO_CAMERA_FRAMES,
                CODE_NO_FRAMES,
                "camera.live is connected and the decoder has 0 frames",
            )
        }
        if (snapshot.rawDetections > 0) {
            return OverlayDiagnosis(
                OverlayHop.BOXES_HIDDEN,
                "BOXES HIDDEN",
                "The detector returned ${snapshot.rawDetections}, and none matched the newest frame",
            )
        }
        if (snapshot.inferencesCompleted > 0) {
            return OverlayDiagnosis(
                OverlayHop.ZERO_OBJECTS,
                CODE_ZERO,
                "Frames are arriving and the detector returned no box",
            )
        }
        return OverlayDiagnosis(
            OverlayHop.WAITING_FOR_DETECTOR,
            "WAITING FOR DETECTOR",
            "Frames are arriving and the detector has not finished a pass",
        )
    }

    /** Short lines for the corner mark. Empty when the mark is turned off. */
    fun probeLines(snapshot: OverlaySnapshot, diagnosis: OverlayDiagnosis): List<String> {
        if (!snapshot.probeEnabled) return emptyList()
        val metric = "det%d pub%d drw%d".format(
            Locale.US, snapshot.rawDetections, snapshot.freshBoxes, snapshot.drawnBoxes,
        )
        return listOf("v${snapshot.versionName} #${snapshot.versionCode}", diagnosis.code, metric)
    }

    fun notificationLine(
        snapshot: OverlaySnapshot,
        diagnosis: OverlayDiagnosis,
        extra: String = "",
    ): String {
        val head = "${snapshot.packageName} ${snapshot.versionName} (${snapshot.versionCode}) | " +
            "${diagnosis.code} | ${diagnosis.detail}"
        val tail = extra.trim()
        return if (tail.isEmpty()) head else "$head | $tail"
    }
}
