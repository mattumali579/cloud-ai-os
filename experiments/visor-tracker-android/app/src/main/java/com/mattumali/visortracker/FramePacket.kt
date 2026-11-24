package com.mattumali.visortracker

import android.graphics.RectF
import com.mattumali.visortracker.CameraSpatial3D.CameraIntrinsics

/**
 * Single unified FramePacket contract for Visor (GitHub Issue #40).
 *
 * Provides immutable provenance for every frame flowing from Visor VDK LiveVideoDecoder
 * to inference models, motion tracker, and overlay view:
 * - Monotonic frame sequence and capture clock (separate from inference/render clocks).
 * - Original dimensions, rotation, crop, and mirror transforms.
 * - Camera intrinsics and calibration status.
 */
data class FramePacket(
    val sequence: Long,
    val captureMonoMs: Long,
    val width: Int,
    val height: Int,
    val rotation: Int = 0,
    val crop: RectF? = null,
    val isMirrored: Boolean = false,
    val intrinsics: CameraIntrinsics = CameraIntrinsics.forViewport(width, height),
    val sourceBackend: String = "Visor_VDK_LiveVideoDecoder",
) {
    val aspectRatio: Float get() = if (height > 0) width.toFloat() / height.toFloat() else 1.0f

    companion object {
        fun create(
            sequence: Long,
            captureMonoMs: Long,
            width: Int,
            height: Int,
            rotation: Int = 0,
        ): FramePacket = FramePacket(
            sequence = sequence,
            captureMonoMs = captureMonoMs,
            width = width,
            height = height,
            rotation = rotation,
            intrinsics = CameraIntrinsics.forViewport(width, height),
        )
    }
}
