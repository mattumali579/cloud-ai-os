package com.mattumali.visortracker

import android.content.Context
import android.graphics.Bitmap
import android.util.Log
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/**
 * Opt-in private capture path for raw camera frames BEFORE drawing the overlay.
 *
 * Requirements (Issue #40 / Stage 0):
 * - Captures pre-overlay raw camera frames with precise timestamps.
 * - Stored exclusively in local, private application storage (`filesDir/private_captures`).
 * - ZERO automatic network upload or transmission.
 * - Explicit user-controlled deletion (`clearAllCaptures`).
 * - Bounded storage footprint (ring buffer up to maxCaptures to prevent disk exhaustion).
 */
class PrivateFrameCapture(
    context: Context? = null,
    val storageDir: File = File(context?.filesDir ?: File(System.getProperty("java.io.tmpdir") ?: "."), "private_captures"),
    private val maxCaptures: Int = 100,
) {
    private val executor = Executors.newSingleThreadExecutor()

    init {
        storageDir.mkdirs()
    }

    /**
     * Records a raw frame and its detection metadata if opt-in capture is active.
     */
    fun captureFrame(
        bitmap: Bitmap?,
        sequence: Long,
        capturedAtMs: Long,
        detections: List<BoxObservation>,
    ) {
        executor.execute {
            try {
                enforceStorageLimit()
                val dateFormat = SimpleDateFormat("yyyyMMdd_HHmmss_SSS", Locale.US)
                val timestampStr = dateFormat.format(Date(if (capturedAtMs > 0) capturedAtMs else System.currentTimeMillis()))
                val baseName = "frame_${timestampStr}_seq${sequence}"

                val w = bitmap?.width ?: 640
                val h = bitmap?.height ?: 480
                if (bitmap != null) {
                    val imgFile = File(storageDir, "$baseName.jpg")
                    FileOutputStream(imgFile).use { out ->
                        bitmap.compress(Bitmap.CompressFormat.JPEG, 92, out)
                    }
                }

                val metaFile = File(storageDir, "$baseName.json")
                val jsonContent = buildMetadataJson(sequence, capturedAtMs, w, h, detections)
                metaFile.writeText(jsonContent)
            } catch (e: Exception) {
                Log.w("PrivateFrameCapture", "Failed to write private capture frame", e)
            }
        }
    }

    fun captureCount(): Int {
        val files = storageDir.listFiles { f -> f.extension == "json" }
        return files?.size ?: 0
    }

    fun clearAllCaptures(): Boolean {
        return try {
            val files = storageDir.listFiles() ?: return true
            for (f in files) {
                f.delete()
            }
            true
        } catch (e: Exception) {
            Log.e("PrivateFrameCapture", "Error clearing private captures", e)
            false
        }
    }

    private fun enforceStorageLimit() {
        val metadataFiles = storageDir.listFiles { f -> f.extension == "json" } ?: return
        if (metadataFiles.size >= maxCaptures) {
            val sorted = metadataFiles.sortedBy { it.lastModified() }
            val toDeleteCount = minOf(10, sorted.size)
            for (i in 0 until toDeleteCount) {
                val meta = sorted[i]
                val img = File(storageDir, meta.nameWithoutExtension + ".jpg")
                meta.delete()
                if (img.exists()) img.delete()
            }
        }
    }

    private fun buildMetadataJson(
        seq: Long,
        capturedAtMs: Long,
        width: Int,
        height: Int,
        boxes: List<BoxObservation>,
    ): String = buildString {
        append("{\n")
        append("  \"sequence\": $seq,\n")
        append("  \"captured_at_ms\": $capturedAtMs,\n")
        append("  \"provenance\": \"raw_vdk_camera_live_pre_overlay\",\n")
        append("  \"width\": $width,\n")
        append("  \"height\": $height,\n")
        append("  \"boxes\": [\n")
        for ((idx, b) in boxes.withIndex()) {
            append("    {\"kind\": \"${b.kind}\", \"label\": \"${b.label}\", \"score\": ${b.score}, \"track_id\": ${b.trackId}}")
            if (idx < boxes.size - 1) append(",")
            append("\n")
        }
        append("  ]\n")
        append("}")
    }
}
