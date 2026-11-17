package com.mattumali.visortracker

import android.content.Context
import android.graphics.Bitmap
import android.graphics.RectF
import android.util.Log
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.facedetector.FaceDetector
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarker
import com.google.mediapipe.tasks.vision.objectdetector.ObjectDetector
import kotlin.math.max
import kotlin.math.min

data class FrameDetections(
    val width: Int,
    val height: Int,
    val boxes: List<BoxObservation>,
    val sequence: Long = 0,
    val capturedAtMs: Long = 0,
)

/**
 * Runs inference without rendering another camera preview. The native Visor video remains
 * independent, while the inexpensive transparent overlay draws the returned boxes.
 *
 * Called on one dedicated worker thread. Never invoke detect() concurrently.
 */
class VisionPipeline(
    context: Context,
    private val includeHandsFaces: Boolean = true,
    private val preset: DetectionPreset = DetectionPreset.FAST
) : AutoCloseable {
    private val tracks = TrackIds()
    private val objects: ObjectDetector
    private val hands: HandLandmarker?
    private val faces: FaceDetector?
    private var frameNumber = 0
    private var recentHands = emptyList<BoxObservation>()
    private var recentFaces = emptyList<BoxObservation>()

    init {
        val app = context.applicationContext
        objects = ObjectDetector.createFromOptions(
            app,
            ObjectDetector.ObjectDetectorOptions.builder()
                .setBaseOptions(BaseOptions.builder().setModelAssetPath(preset.modelAsset).build())
                .setRunningMode(RunningMode.IMAGE)
                .setMaxResults(preset.maxResults)
                .setScoreThreshold(preset.scoreThreshold)
                .build()
        )
        hands = if (includeHandsFaces) HandLandmarker.createFromOptions(
            app,
            HandLandmarker.HandLandmarkerOptions.builder()
                .setBaseOptions(BaseOptions.builder().setModelAssetPath("hand_landmarker.task").build())
                .setRunningMode(RunningMode.IMAGE)
                .setNumHands(2)
                .setMinHandDetectionConfidence(0.45f)
                .setMinHandPresenceConfidence(0.45f)
                .setMinTrackingConfidence(0.45f)
                .build()
        ) else null
        faces = if (includeHandsFaces) FaceDetector.createFromOptions(
            app,
            FaceDetector.FaceDetectorOptions.builder()
                .setBaseOptions(BaseOptions.builder().setModelAssetPath("blaze_face_full_range.tflite").build())
                .setRunningMode(RunningMode.IMAGE)
                .setMinDetectionConfidence(0.5f)
                .build()
        ) else null
    }

    fun detect(input: Bitmap): FrameDetections {
        // Reducing input pixels cuts resize/conversion overhead before MediaPipe.
        val width = min(preset.maxFrameWidth, input.width)
        val height = max(1, (input.height.toDouble() * width / input.width).toInt())
        val resized = if (input.width != width || input.height != height) {
            Bitmap.createScaledBitmap(input, width, height, true)
        } else input

        try {
            val image = BitmapImageBuilder(resized).build()
            val observed = mutableListOf<BoxObservation>()
            objects.detect(image).detections().forEach { detection ->
                val category = detection.categories().maxByOrNull { it.score() }
                if (category != null) {
                    observed += detection.boundingBox().box(
                        "object", category.categoryName().ifBlank { "object" }, category.score()
                    )
                }
            }

            // Expensive models are staggered: object detection stays the priority.
            // Cache other classes between their scans so boxes don't flicker off.
            if (includeHandsFaces && frameNumber % 2 == 0) {
                recentHands = hands?.detect(image)?.landmarks()?.mapNotNull { hand ->
                    if (hand.isEmpty()) null else {
                        val coords = hand.map { it.x() * width to it.y() * height }
                        BoxObservation(
                            kind = "hand", label = "hand", score = 1f,
                            left = coords.minOf { it.first }, top = coords.minOf { it.second },
                            right = coords.maxOf { it.first }, bottom = coords.maxOf { it.second },
                            points = coords
                        )
                    }
                } ?: emptyList()
            }
            if (includeHandsFaces && frameNumber % 4 == 0) {
                recentFaces = faces?.detect(image)?.detections()?.map { detection ->
                    detection.boundingBox().box("face", "face", detection.categories().firstOrNull()?.score() ?: 0f)
                } ?: emptyList()
            }
            frameNumber++
            observed += recentHands
            observed += recentFaces
            // copy avoids mutations of cached detection objects after delivery to the UI.
            return FrameDetections(width, height, tracks.update(observed).map { it.copy(points = it.points.toList()) })
        } catch (error: Exception) {
            Log.e(TAG, "MediaPipe detection failed", error)
            throw error
        } finally {
            if (resized !== input) resized.recycle()
        }
    }

    private fun RectF.box(kind: String, label: String, confidence: Float) =
        BoxObservation(kind, label, confidence, left, top, right, bottom)

    override fun close() {
        hands?.close()
        faces?.close()
        objects.close()
    }

    companion object {
        private const val TAG = "VisorTracker"
    }
}
