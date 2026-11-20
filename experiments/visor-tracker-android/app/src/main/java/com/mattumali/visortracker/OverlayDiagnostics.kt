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
    DETECTOR_RUNNING,
    NO_GRAY,
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
    val grayFrames: Int = 0,
    val detectorBusy: Boolean = false,
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
        if (snapshot.drawnBoxes > 0) {
            val age = if (snapshot.boxAgeMs < 0) "age n/a" else "age ${snapshot.boxAgeMs} ms"
            return OverlayDiagnosis(
                OverlayHop.TRACKING,
                "TRACKING",
                "${snapshot.drawnBoxes} boxes, $age",
            )
        }
        // A published or detected box that never became pixels is not TRACKING.
        // Saying TRACKING here hid the miss behind a green proof mark.
        if (snapshot.freshBoxes > 0 || snapshot.rawDetections > 0) {
            val found = maxOf(snapshot.freshBoxes, snapshot.rawDetections)
            return OverlayDiagnosis(
                OverlayHop.BOXES_HIDDEN,
                "BOXES HIDDEN",
                "The detector returned $found, and the overlay is not painting a box",
            )
        }
        if (failure != null) {
            // StageFaults stores a detector fault as "detect: …". That text does not
            // start with "MediaPipe", and the service message also contains "Decode/detection".
            // A hand or face failure is "hands: …" or "faces: …" after object boxes return.
            val detector = failure.startsWith("MediaPipe") ||
                failure.startsWith("detect:") ||
                failure.startsWith("hands:") ||
                failure.startsWith("faces:") ||
                failure.contains("Decode/detection")
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
        if (snapshot.grayFrames <= 0) {
            return OverlayDiagnosis(
                OverlayHop.NO_GRAY,
                "NO GRAY FRAME",
                "Video frames arrived but grayscale capture produced 0",
            )
        }
        if (snapshot.inferencesCompleted <= 0 && snapshot.detectorBusy) {
            return OverlayDiagnosis(
                OverlayHop.DETECTOR_RUNNING,
                "DETECTOR RUNNING",
                "The first object pass has started and has not returned yet",
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

    /**
     * Whether [frameSequence] may change what the overlay paints.
     * A newer frame always may. An older one may not clear the screen or slide
     * a box backward. It may fill a blank overlay, or replace a painted box
     * that is not the current lock, when the capture is still recent.
     */
    fun acceptFrame(
        frameSequence: Long,
        shownSequence: Long,
        incomingBoxes: Int,
        showingTrackId: Int?,
        incomingTrackId: Int?,
        lockedId: Int?,
        captureIsRecent: Boolean,
    ): Boolean {
        val stale = frameSequence < shownSequence
        if (!stale) return true
        if (incomingBoxes <= 0 || !captureIsRecent) return false
        if (showingTrackId == null) return true
        return lockedId != null && incomingTrackId == lockedId && showingTrackId != lockedId
    }

    /** Accepted frames move the watermark forward. A stale fill must not move it backward. */
    fun nextShownSequence(shown: Long, frame: Long, accepted: Boolean): Long {
        if (!accepted || frame < shown) return shown
        return frame
    }

    /** A missing clock or timestamp must not be the reason the only box is dropped. */
    fun captureIsRecent(nowMs: Long, capturedAtMs: Long, windowMs: Long): Boolean {
        if (capturedAtMs <= 0L || nowMs <= 0L) return true
        val age = nowMs - capturedAtMs
        return age in 0..windowMs
    }

    /** Milliseconds from capture to now. Negative and overflowing deltas become a safe bound. */
    fun ageMs(nowMs: Long, capturedAtMs: Long): Int {
        if (nowMs <= 0L || capturedAtMs <= 0L) return 0
        val delta = nowMs - capturedAtMs
        if (delta <= 0L) return 0
        return if (delta > Int.MAX_VALUE) Int.MAX_VALUE else delta.toInt()
    }

    /**
     * Held boxes were measured in [previous]'s pixel space. Projecting them with
     * a later frame's width and height throws the lock off the screen.
     */
    fun frameToDraw(
        candidate: FrameDetections,
        previous: FrameDetections?,
        incomingBoxes: Int,
        kept: List<BoxObservation>,
    ): FrameDetections {
        if (incomingBoxes == 0 && kept.isNotEmpty() && previous != null) {
            return previous.copy(boxes = kept)
        }
        return candidate.copy(boxes = kept)
    }

    fun hasPaintableObject(boxes: List<BoxObservation>): Boolean = boxes.any {
        it.kind == "object" && !it.held && it.trackId > 0 && it.right > it.left && it.bottom > it.top
    }
}
