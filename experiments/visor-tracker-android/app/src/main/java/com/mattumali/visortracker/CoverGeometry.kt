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
}
