package com.mattumali.visortracker

/**
 * Accuracy is a deliberate user preference, not an automatic thermal runaway.
 * Lite2 has 448px input vs Lite0's 320px; it can recognize more difficult
 * examples, but inference uses substantially more CPU on phones.
 */
enum class DetectionPreset(
    val modelAsset: String,
    val maxFrameWidth: Int,
    val scoreThreshold: Float,
    val maxResults: Int,
) {
    FAST("efficientdet_lite0.tflite", 384, 0.25f, 10),
    ACCURATE("efficientdet_lite2.tflite", 512, 0.25f, 12);

    companion object {
        fun read(name: String?): DetectionPreset =
            entries.firstOrNull { it.name == name } ?: FAST
    }
}
