package com.mattumali.visortracker

/**
 * Accuracy is a deliberate user preference, not an automatic thermal runaway.
 * Lite2 has 448px input vs Lite0's 320px; it can recognize more difficult
 * examples, but inference uses substantially more CPU on phones.
 *
 * [maxResults] is a detector candidate pool, not the number of boxes drawn.
 * The overlay draws one object. A pool of eight is only so pointing can
 * choose the center candidate. It does not make inference faster.
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
