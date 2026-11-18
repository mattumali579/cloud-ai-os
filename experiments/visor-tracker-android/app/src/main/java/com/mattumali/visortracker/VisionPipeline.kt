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
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarkerResult
import com.google.mediapipe.tasks.vision.objectdetector.ObjectDetector
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

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
 * Object detection stays on the CPU delegate. The hand landmarker tries the GPU delegate
 * once; if that create or the first detect does not return, later frames use the CPU
 * landmarker. Do not call [detect] concurrently. A hands-only pass reuses the last object
 * boxes so a finger can move while EfficientDet is skipped.
 */
class VisionPipeline(
    context: Context,
    private val includeFaces: Boolean = false,
    private val preset: DetectionPreset = DetectionPreset.FAST,
    private val includeHands: Boolean = false,
) : AutoCloseable {
    private val tracks = TrackIds()
    private val objects: ObjectDetector
    private val handRuntime: HandRuntime?
    private val faces: FaceDetector?
    private var frameNumber = 0
    private var recentObjects = emptyList<BoxObservation>()
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

    /** Winner after init and, if needed, the first real hand detect. */
    val handDelegate: String get() = handRuntime?.delegateName ?: "none"
    val handDelegateAttempt: String get() = handRuntime?.attempt ?: "none"
    val handFallbacks: Int get() = handRuntime?.fallbacks ?: 0

    /** EfficientDet stays on CPU. A GPU detect that never returns is not used. */
    val objectDelegate: String = "CPU"

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
        handRuntime = if (includeHands) HandRuntime(app) else null
        faces = if (includeFaces) FaceDetector.createFromOptions(
            app,
            FaceDetector.FaceDetectorOptions.builder()
                .setBaseOptions(BaseOptions.builder().setModelAssetPath("blaze_face_full_range.tflite").build())
                .setRunningMode(RunningMode.IMAGE)
                .setMinDetectionConfidence(0.5f)
                .build()
        ) else null
        Log.i(TAG, "delegates hand=$handDelegate attempted=$handDelegateAttempt object=$objectDelegate")
    }

    /** Set when hands or faces fail. Object boxes from that frame are still returned. */
    var lastOptionalFault: String? = null
        private set

    fun detect(
        input: Bitmap,
        runObjects: Boolean = true,
        runHands: Boolean = includeHands,
    ): FrameDetections {
        if (input.isRecycled) throw IllegalStateException("recycled frame")
        val (width, height) = YuvSnapshot.outputSize(input.width, input.height, preset.maxFrameWidth)
        val doHands = runHands && handRuntime != null
        val (handWidth, handHeight) = if (doHands) {
            YuvSnapshot.outputSize(input.width, input.height, HAND_FRAME)
        } else {
            width to height
        }
        val objectBitmap = if (!runObjects) {
            null
        } else if (input.width != width || input.height != height) {
            Bitmap.createScaledBitmap(input, width, height, true)
        } else {
            input
        }
        val handBitmap = when {
            !doHands -> null
            objectBitmap != null && handWidth == objectBitmap.width && handHeight == objectBitmap.height -> objectBitmap
            input.width == handWidth && input.height == handHeight -> input
            else -> Bitmap.createScaledBitmap(input, handWidth, handHeight, true)
        }

        try {
            val observed = mutableListOf<BoxObservation>()
            if (runObjects) {
                val source = objectBitmap ?: input
                val image = BitmapImageBuilder(source).build()
                try {
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
                    recentObjects = observed.map { it.copy() }
                } finally {
                    image.close()
                }
            } else {
                observed += recentObjects.map { it.copy(points = it.points.toList()) }
            }

            var optionalFailed = false
            var scannedOptional = false
            if (doHands) {
                scannedOptional = true
                val handStarted = android.os.SystemClock.elapsedRealtime()
                recentHands = scanHands(handBitmap ?: input, width, height) {
                    optionalFailed = true
                    lastOptionalFault = it
                }
                lastHandMs = android.os.SystemClock.elapsedRealtime() - handStarted
                handInferences += 1
            }
            if (runObjects && includeFaces && frameNumber % 4 == 0) {
                scannedOptional = true
                val source = objectBitmap ?: input
                val image = BitmapImageBuilder(source).build()
                try {
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
                } finally {
                    image.close()
                }
            }
            if (runObjects) frameNumber++
            if (scannedOptional && !optionalFailed) lastOptionalFault = null
            observed += recentHands
            observed += recentFaces
            val tracked = tracks.update(observed).filter { !it.held }
            return FrameDetections(width, height, tracked.map { it.copy(points = it.points.toList()) })
        } catch (error: Exception) {
            Log.e(TAG, "MediaPipe detection failed", error)
            throw error
        } finally {
            val runtime = handRuntime
            val leakHand = runtime?.abandonBitmap == true
            if (runtime != null && runtime.abandonBitmap) runtime.abandonBitmap = false
            if (handBitmap != null && handBitmap !== objectBitmap && handBitmap !== input &&
                !handBitmap.isRecycled && !leakHand
            ) {
                handBitmap.recycle()
            }
            if (objectBitmap != null && objectBitmap !== input && !objectBitmap.isRecycled) objectBitmap.recycle()
        }
    }

    private fun scanHands(
        bitmap: Bitmap,
        objectWidth: Int,
        objectHeight: Int,
        onFault: (String) -> Unit,
    ): List<BoxObservation> {
        val runtime = handRuntime ?: return emptyList()
        var marked = try {
            runtime.detect(bitmap)
        } catch (error: Exception) {
            onFault("hands: " + (error.message ?: error.javaClass.simpleName))
            Log.e(TAG, "hands failed", error)
            return emptyList()
        }
        if (marked == null && runtime.consumeRetry()) {
            marked = try {
                runtime.detect(bitmap)
            } catch (error: Exception) {
                onFault("hands: " + (error.message ?: error.javaClass.simpleName))
                Log.e(TAG, "hands failed", error)
                return emptyList()
            }
        }
        if (marked == null) return emptyList()
        return handsToBoxes(marked, objectWidth, objectHeight)
    }

    private fun handsToBoxes(
        marked: HandLandmarkerResult,
        width: Int,
        height: Int,
    ): List<BoxObservation> {
        val handed = marked.handedness()
        return marked.landmarks().mapIndexedNotNull { index, hand ->
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
            val coords = hand.map { it.x() * width to it.y() * height }
            BoxObservation(
                kind = "hand", label = "hand", score = score,
                left = coords.minOf { it.first }, top = coords.minOf { it.second },
                right = coords.maxOf { it.first }, bottom = coords.maxOf { it.second },
                points = coords,
            )
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
        handRuntime?.close()
        faces?.close()
        objects.close()
    }

    private class HandRuntime(private val app: Context) : AutoCloseable {
        var delegateName: String = "CPU"
            private set
        var attempt: String = "CPU"
            private set
        var fallbacks: Int = 0
            private set
        var abandonBitmap: Boolean = false
        private var retryCpu = false
        private var marker: HandLandmarker? = null
        private var executor = Executors.newSingleThreadExecutor { runnable ->
            Thread(runnable, "visor-hand").apply { isDaemon = true }
        }
        private var handOnExecutor = false
        private var gpuProbed = false

        init {
            attempt = "GPU"
            val created = try {
                executor.submit<HandLandmarker?> {
                    try {
                        create(Delegate.GPU)
                    } catch (error: Exception) {
                        Log.w(TAG, "GPU hand landmarker did not initialize", error)
                        null
                    }
                }.get(CREATE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            } catch (error: Exception) {
                Log.w(TAG, "GPU hand landmarker did not return", error)
                null
            }
            if (created != null) {
                marker = created
                delegateName = "GPU"
                handOnExecutor = true
            } else {
                abandonExecutor()
                marker = create(Delegate.CPU)
                delegateName = "CPU"
                handOnExecutor = false
                gpuProbed = true
                fallbacks = 1
            }
        }

        fun detect(bitmap: Bitmap): HandLandmarkerResult? {
            val current = marker ?: return null
            if (!handOnExecutor) {
                val image = BitmapImageBuilder(bitmap).build()
                return try {
                    current.detect(image)
                } finally {
                    image.close()
                }
            }
            val timeout = if (!gpuProbed) PROBE_TIMEOUT_MS else DETECT_TIMEOUT_MS
            val owned = if (!gpuProbed) {
                bitmap.copy(bitmap.config ?: Bitmap.Config.ARGB_8888, false)
            } else {
                bitmap
            }
            val future = executor.submit<HandLandmarkerResult> {
                val image = BitmapImageBuilder(owned).build()
                try {
                    current.detect(image)
                } finally {
                    image.close()
                }
            }
            return try {
                val value = future.get(timeout, TimeUnit.MILLISECONDS)
                gpuProbed = true
                if (owned !== bitmap && !owned.isRecycled) owned.recycle()
                value
            } catch (error: TimeoutException) {
                future.cancel(true)
                if (owned !== bitmap) abandonBitmap = true
                fallbackToCpu()
                null
            } catch (error: Exception) {
                if (owned !== bitmap && !owned.isRecycled) owned.recycle()
                val cause = (error as? ExecutionException)?.cause ?: error
                if (delegateName == "GPU") {
                    Log.w(TAG, "GPU hand detect failed", cause)
                    fallbackToCpu()
                    null
                } else {
                    throw cause
                }
            }
        }

        fun consumeRetry(): Boolean {
            if (!retryCpu) return false
            retryCpu = false
            return marker != null
        }

        private fun fallbackToCpu() {
            if (delegateName == "CPU") return
            delegateName = "CPU"
            handOnExecutor = false
            gpuProbed = true
            fallbacks += 1
            abandonExecutor()
            marker = try {
                create(Delegate.CPU)
            } catch (error: Exception) {
                Log.e(TAG, "CPU hand landmarker failed", error)
                null
            }
            retryCpu = marker != null
            Log.i(TAG, "hand delegate fell back to CPU")
        }

        private fun abandonExecutor() {
            executor.shutdown()
        }

        private fun create(delegate: Delegate): HandLandmarker =
            HandLandmarker.createFromOptions(
                app,
                HandLandmarker.HandLandmarkerOptions.builder()
                    .setBaseOptions(
                        BaseOptions.builder()
                            .setModelAssetPath("hand_landmarker.task")
                            .setDelegate(delegate)
                            .build()
                    )
                    .setRunningMode(RunningMode.IMAGE)
                    .setNumHands(2)
                    .setMinHandDetectionConfidence(IndexPoint.MIN_SCORE)
                    .setMinHandPresenceConfidence(IndexPoint.MIN_SCORE)
                    .setMinTrackingConfidence(IndexPoint.MIN_SCORE)
                    .build()
            )

        override fun close() {
            val closing = marker
            marker = null
            executor.shutdown()
            if (closing == null) return
            val closer = Executors.newSingleThreadExecutor()
            try {
                closer.submit { closing.close() }.get(CLOSE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            } catch (error: Exception) {
                Log.w(TAG, "hand landmarker close did not return", error)
            } finally {
                closer.shutdownNow()
            }
        }
    }

    companion object {
        private const val TAG = "VisorTracker"
        internal const val HAND_FRAME = 640
        private const val CREATE_TIMEOUT_MS = 3_000L
        private const val PROBE_TIMEOUT_MS = 2_500L
        private const val DETECT_TIMEOUT_MS = 8_000L
        private const val CLOSE_TIMEOUT_MS = 2_000L

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
