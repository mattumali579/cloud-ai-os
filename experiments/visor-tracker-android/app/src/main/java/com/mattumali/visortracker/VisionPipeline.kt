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
    private val includeFaces: Boolean = false,
    private val preset: DetectionPreset = DetectionPreset.FAST,
    private val includeHands: Boolean = false,
) : AutoCloseable {
    private val tracks = TrackIds()
    private val objects: ObjectDetector
    private val hands: HandLandmarker?
    private val faces: FaceDetector?
    private var frameNumber = 0
    private var recentHands = emptyList<BoxObservation>()
    private var recentFaces = emptyList<BoxObservation>()
    var objectInferences: Int = 0
        private set
    var handInferences: Int = 0
        private set
    var lastObjectMs: Long = 0
        private set
    var lastHandMs: Long = 0
        private set

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
        hands = if (includeHands) HandLandmarker.createFromOptions(
            app,
            HandLandmarker.HandLandmarkerOptions.builder()
                .setBaseOptions(
                    BaseOptions.builder()
                        .setModelAssetPath("hand_landmarker.task")
                        .setDelegate(Delegate.CPU)
                        .build()
                )
                .setRunningMode(RunningMode.IMAGE)
                .setNumHands(2)
                .setMinHandDetectionConfidence(0.40f)
                .setMinHandPresenceConfidence(0.40f)
                .setMinTrackingConfidence(0.40f)
                .build()
        ) else null
        faces = if (includeFaces) FaceDetector.createFromOptions(
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
        val (handWidth, handHeight) = if (includeHands) {
            YuvSnapshot.outputSize(input.width, input.height, HAND_FRAME)
        } else {
            width to height
        }
        val handBitmap = if (!includeHands || (handWidth == resized.width && handHeight == resized.height)) {
            resized
        } else {
            Bitmap.createScaledBitmap(input, handWidth, handHeight, true)
        }

        try {
            val image = BitmapImageBuilder(resized).build()
            val handImage = if (handBitmap === resized) image else BitmapImageBuilder(handBitmap).build()
            try {
            val observed = mutableListOf<BoxObservation>()
            // Close the native image after every call. Leaving it open crashed
            // the second EfficientDet pass on the emulator.
            val objectStarted = android.os.SystemClock.elapsedRealtime()
            val detected = objects.detect(image)
            lastObjectMs = android.os.SystemClock.elapsedRealtime() - objectStarted
            objectInferences += 1
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
            if (includeHands) {
                scannedOptional = true
                var failed = false
                val handStarted = android.os.SystemClock.elapsedRealtime()
                recentHands = runOptional("hands", {
                    failed = true
                    lastOptionalFault = it
                }) {
                    val marked = hands?.detect(handImage) ?: return@runOptional emptyList()
                    val handed = marked.handedness()
                    marked.landmarks().mapIndexedNotNull { index, hand ->
                        if (hand.size < 21) return@mapIndexedNotNull null
                        val score = handed.getOrNull(index)?.firstOrNull()?.score() ?: 0f
                        if (score < IndexPoint.MIN_SCORE) return@mapIndexedNotNull null
                        val needed = intArrayOf(
                            IndexPoint.WRIST,
                            IndexPoint.INDEX_MCP,
                            IndexPoint.INDEX_PIP,
                            IndexPoint.INDEX_DIP,
                            IndexPoint.INDEX_TIP,
                        )
                        if (needed.any { joint -> !landmarkReliable(hand[joint]) }) return@mapIndexedNotNull null
                        // Landmarks are normalized to the hand image. That image is a
                        // uniform scale of the object frame, so object-frame pixels match.
                        val coords = hand.map { it.x() * width to it.y() * height }
                        BoxObservation(
                            kind = "hand", label = "hand", score = score,
                            left = coords.minOf { it.first }, top = coords.minOf { it.second },
                            right = coords.maxOf { it.first }, bottom = coords.maxOf { it.second },
                            points = coords,
                        )
                    }
                }
                lastHandMs = android.os.SystemClock.elapsedRealtime() - handStarted
                handInferences += 1
                optionalFailed = failed
            }
            if (includeFaces && frameNumber % 4 == 0) {
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
                if (handImage !== image) handImage.close()
                image.close()
            }
        } catch (error: Exception) {
            Log.e(TAG, "MediaPipe detection failed", error)
            throw error
        } finally {
            if (handBitmap !== resized && handBitmap !== input && !handBitmap.isRecycled) handBitmap.recycle()
            if (resized !== input && !resized.isRecycled) resized.recycle()
        }
    }

    private fun landmarkReliable(landmark: com.google.mediapipe.tasks.components.containers.NormalizedLandmark): Boolean {
        val presence = landmark.presence().orElse(1f)
        val visibility = landmark.visibility().orElse(1f)
        return presence >= IndexPoint.MIN_SCORE && visibility >= IndexPoint.MIN_SCORE
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
        private const val HAND_FRAME = 640

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
