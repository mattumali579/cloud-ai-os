package com.mattumali.visortracker

/**
 * The shift measured on this frame, applied on this frame.
 * A gain below 1 would leave the box behind the patch and catch up over later frames.
 */
object MeasuredSmooth {
    const val GAIN = 1f

    fun translation(dx: Float, dy: Float): Pair<Float, Float> = dx * GAIN to dy * GAIN
}
