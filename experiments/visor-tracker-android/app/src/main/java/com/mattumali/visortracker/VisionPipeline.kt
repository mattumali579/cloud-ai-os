package com.mattumali.visortracker

import android.content.Context
import android.graphics.Bitmap
import android.graphics.RectF
import android.util.Log
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.core.Delegate
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.facedetector.FaceDetector
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarker
import com.google.mediapipe.tasks.vision.objectdetector.ObjectDetector

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
    private val includeHandsFaces: Boolean = false,
    private val preset: DetectionPreset = DetectionPreset.FAST,
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
                .setBaseOptions(
                    BaseOptions.builder()
                        .setModelAssetPath(preset.modelAsset)
                        .setDelegate(Delegate.CPU)
                        .build()
                )
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

    /** Set when hands or faces fail. Object boxes from that frame are still returned. */
    var lastOptionalFault: String? = null
        private set

    fun detect(input: Bitmap): FrameDetections {
        if (input.isRecycled) throw IllegalStateException("recycled frame")
        // Same geometry as the YUV snapshot. A zero-size frame fails here instead
        // of becoming a one-pixel bitmap the detector treats as "no objects".
        val (width, height) = YuvSnapshot.outputSize(input.width, input.height, preset.maxFrameWidth)
        val resized = if (input.width != width || input.height != height) {
            Bitmap.createScaledBitmap(input, width, height, true)
        } else input

        try {
            val image = BitmapImageBuilder(resized).build()
            try {
            val observed = mutableListOf<BoxObservation>()
            // Close the native image after every call. Leaving it open crashed
            // the second EfficientDet pass on the emulator.
            val detected = objects.detect(image)
            detected.detections().forEach { detection ->
                val categories = detection.categories().map { category ->
                    CategoryScore(category.categoryName().orEmpty(), category.score())
                }
                val chosen = LabelPolicy.selection(categories) ?: return@forEach
                observed += detection.boundingBox().box("object", chosen.name, chosen.score)
            }

            // Expensive models are staggered: object detection stays the priority.
            // A hand or face failure must not throw away the objects already found.
            var optionalFailed = false
            var scannedOptional = false
            if (includeHandsFaces && frameNumber % 2 == 0) {
                scannedOptional = true
                var failed = false
                recentHands = runOptional("hands", {
                    failed = true
                    lastOptionalFault = it
                }) {
                    hands?.detect(image)?.landmarks()?.mapNotNull { hand ->
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
                optionalFailed = failed
            }
            if (includeHandsFaces && frameNumber % 4 == 0) {
                scannedOptional = true
                var failed = false
                recentFaces = runOptional("faces", {
                    failed = true
                    lastOptionalFault = it
                }) {
                    faces?.detect(image)?.detections()?.map { detection ->
                        detection.boundingBox().box(
                            "face", "face", detection.categories().firstOrNull()?.score() ?: 0f,
                        )
                    } ?: emptyList()
                }
                optionalFailed = optionalFailed || failed
            }
            if (scannedOptional && !optionalFailed) lastOptionalFault = null
            frameNumber++
            observed += recentHands
            observed += recentFaces
            // Drop grace-held rectangles before display. TrackIds still remembers
            // the id internally; painting the hold would freeze the box in place.
            // Every live object stays in this result so the center one can be
            // drawn and the lock can see a different object. The overlay paints one.
            val tracked = tracks.update(observed).filter { !it.held }
            return FrameDetections(width, height, tracked.map { it.copy(points = it.points.toList()) })
            } finally {
                image.close()
            }
        } catch (error: Exception) {
            Log.e(TAG, "MediaPipe detection failed", error)
            throw error
        } finally {
            if (resized !== input && !resized.isRecycled) resized.recycle()
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

        /**
         * Hands and faces are optional. Their failure is reported and the object
         * boxes already found stay in the frame.
         */
        internal fun runOptional(
            stage: String,
            onFault: (String) -> Unit,
            block: () -> List<BoxObservation>,
        ): List<BoxObservation> =
            try {
                block()
            } catch (error: Exception) {
                onFault(stage + ": " + (error.message ?: error.javaClass.simpleName))
                Log.e(TAG, "$stage failed", error)
                emptyList()
            }
    }
}
