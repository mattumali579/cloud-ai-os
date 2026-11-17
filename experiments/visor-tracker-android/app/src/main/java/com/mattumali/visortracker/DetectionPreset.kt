package com.mattumali.visortracker

/**
 * Accuracy is a deliberate user preference, not an automatic thermal runaway.
 * Lite2 has 448px input vs Lite0's 320px; it can recognize more difficult
 * examples, but inference uses substantially more CPU on phones.
 *
 * [maxResults] is a detector candidate pool, not the number of boxes drawn.
 * The overlay hard-caps display at [NearbyCandidates.DISPLAY_LIMIT]. A pool of
 * eight leaves room to prefer a visually nearer box, and to keep a calibrated
 * target, instead of blindly keeping the five highest scores. Raising the
 * score floor drops the 0.25 clutter that Lite0 was willing to name.
 */
enum class DetectionPreset(
    val modelAsset: String,
    val maxFrameWidth: Int,
    val scoreThreshold: Float,
    val maxResults: Int,
) {
    FAST("efficientdet_lite0.tflite", 384, 0.50f, 8),
    ACCURATE("efficientdet_lite2.tflite", 512, 0.45f, 8);

    companion object {
        fun read(name: String?): DetectionPreset =
            entries.firstOrNull { it.name == name } ?: FAST
    }
}
