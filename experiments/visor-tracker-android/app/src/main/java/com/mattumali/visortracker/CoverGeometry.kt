package com.mattumali.visortracker

import kotlin.math.max

data class ProjectedBox(
    val left: Float,
    val top: Float,
    val right: Float,
    val bottom: Float,
)

/**
 * Visor's full-screen LiveScreen uses coverRect: fill the phone screen,
 * center the picture, and crop the excess. Match that transform exactly.
 * The user must leave Visor's Live View expanded, not in stepped/windowed mode.
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
        val scale = max(screenWidth.toFloat() / frameWidth, screenHeight.toFloat() / frameHeight)
        val leftOffset = (screenWidth - frameWidth * scale) / 2f
        val topOffset = (screenHeight - frameHeight * scale) / 2f
        return ProjectedBox(
            leftOffset + box.left * scale,
            topOffset + box.top * scale,
            leftOffset + box.right * scale,
            topOffset + box.bottom * scale,
        )
    }

    /**
     * True when the cover-cropped box still overlaps the phone screen.
     * A box that lies entirely in the cropped margin is not a drawn overlay.
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
