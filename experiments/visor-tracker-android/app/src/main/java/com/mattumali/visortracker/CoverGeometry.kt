package com.mattumali.visortracker

import kotlin.math.min

data class ProjectedBox(
    val left: Float,
    val top: Float,
    val right: Float,
    val bottom: Float,
)

/**
 * Visor's Live View preserves the 3:4 camera raster and centers it between
 * black bands on the phone. The 10942 screen recording proves a 1080x1440
 * image at y=438 inside a 1080x2316 display. Match that aspect-fit transform.
 */
object CoverGeometry {
    fun project(
        frameWidth: Int,
        frameHeight: Int,
        screenWidth: Int,
        screenHeight: Int,
        box: BoxObservation,
    ): ProjectedBox {
        require(frameWidth > 0 && frameHeight > 0 && screenWidth > 0 && screenHeight > 0)
        val scale = min(screenWidth.toFloat() / frameWidth, screenHeight.toFloat() / frameHeight)
        val leftOffset = (screenWidth - frameWidth * scale) / 2f
        val topOffset = (screenHeight - frameHeight * scale) / 2f
        return ProjectedBox(
            leftOffset + box.left * scale,
            topOffset + box.top * scale,
            leftOffset + box.right * scale,
            topOffset + box.bottom * scale,
        )
    }

    fun projectPoint(
        frameWidth: Int,
        frameHeight: Int,
        screenWidth: Int,
        screenHeight: Int,
        x: Float,
        y: Float,
    ): Pair<Float, Float> {
        require(frameWidth > 0 && frameHeight > 0 && screenWidth > 0 && screenHeight > 0)
        val scale = min(screenWidth.toFloat() / frameWidth, screenHeight.toFloat() / frameHeight)
        val leftOffset = (screenWidth - frameWidth * scale) / 2f
        val topOffset = (screenHeight - frameHeight * scale) / 2f
        return leftOffset + x * scale to topOffset + y * scale
    }

    /**
     * True when a non-empty camera-space box overlaps the aspect-fit viewport.
     */
    fun isVisible(
        frameWidth: Int,
        frameHeight: Int,
        screenWidth: Int,
        screenHeight: Int,
        box: BoxObservation,
    ): Boolean {
        if (frameWidth <= 0 || frameHeight <= 0 || screenWidth <= 0 || screenHeight <= 0) return false
        if (box.right - box.left < 1f || box.bottom - box.top < 1f) return false
        val projected = project(frameWidth, frameHeight, screenWidth, screenHeight, box)
        return projected.left < screenWidth && projected.top < screenHeight &&
            projected.right > 0f && projected.bottom > 0f
    }
}
