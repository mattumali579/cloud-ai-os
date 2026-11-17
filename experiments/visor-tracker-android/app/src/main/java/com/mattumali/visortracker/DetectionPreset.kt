package com.mattumali.visortracker

/**
 * Accuracy is a deliberate user preference, not an automatic thermal runaway.
 * Lite2 has 448px input vs Lite0's 320px; it can recognize more difficult
 * examples, but inference uses substantially more CPU on phones.
 *
 * [maxResults] is a detector candidate pool, not the number of boxes drawn.
 * The overlay hard-caps display at [NearbyCandidates.DISPLAY_LIMIT] and may
 * show fewer. A pool of eight leaves room to keep a calibrated target.
 * [scoreThreshold] is a category-score floor, not a percent of correct labels.
 * 0.50 does not mean the model is right half the time. Fast mode stays Lite0;
 * Lite2 is opt-in because this tree has no paired accuracy/FPS measurement
 * that would justify making it the default.
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
