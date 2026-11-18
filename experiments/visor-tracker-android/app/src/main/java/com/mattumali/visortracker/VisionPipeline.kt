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
import java.util.concurrent.Future
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicInteger

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
 * Object detection stays on the CPU delegate and on the thread that calls [detect].
 * The hand landmarker is owned by one executor. When [overlapModels] is true the two
 * models run at the same time on those two threads, on separate bitmaps. A hand result
 * can be published from that executor before EfficientDet returns.
 *
 * The hand landmarker tries the GPU delegate once when [attemptGpu] is true. If that
 * create or the first detect does not return, later frames use the CPU landmarker.
 * A second GPU attempt is not started. LIVE_STREAM and VIDEO are not enabled: this
 * tree has no repeated-detect probe that survived those modes, and the same mode is
 * not tried again. A hands-only pass reuses the last object boxes.
 */
class VisionPipeline(
    context: Context,
    private val includeFaces: Boolean = false,
    private val preset: DetectionPreset = DetectionPreset.FAST,
    private val includeHands: Boolean = false,
    private val handLongSide: Int = HAND_FRAME,
    private val numHands: Int = HAND_COUNT,
    private val attemptGpu: Boolean = true,
    val overlapModels: Boolean = true,
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
    var lastBitmapMs: Long = 0
        private set
    /** Model time only. Bitmap scaling is [lastBitmapMs]. Overlap makes this closer to the slower model. */
    var lastWallMs: Long = 0
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
        handRuntime = if (includeHands) HandRuntime(app, numHands, attemptGpu) else null
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

    /** Drops one remembered object so a hands-only pass cannot paint it again. */
    fun forgetObject(trackId: Int) {
        if (trackId <= 0) return
        recentObjects = recentObjects.filter { it.trackId != trackId }
    }

    fun detect(
        input: Bitmap,
        runObjects: Boolean = true,
        runHands: Boolean = includeHands,
        onHands: ((List<BoxObservation>) -> Unit)? = null,
    ): FrameDetections {
        if (input.isRecycled) throw IllegalStateException("recycled frame")
        val bitmapStarted = android.os.SystemClock.elapsedRealtime()
        val (width, height) = YuvSnapshot.outputSize(input.width, input.height, preset.maxFrameWidth)
        val doHands = runHands && handRuntime != null
        val (handWidth, handHeight) = if (doHands) {
            YuvSnapshot.outputSize(input.width, input.height, handLongSide)
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
        // Never hand the same bitmap to both models. The hand executor copies again
        // before it returns, so the object thread can read its own pixels.
        val handBitmap = when {
            !doHands -> null
            input.width == handWidth && input.height == handHeight && objectBitmap !== input -> input
            objectBitmap != null && handWidth == objectBitmap.width && handHeight == objectBitmap.height ->
                objectBitmap.copy(Bitmap.Config.ARGB_8888, false)
                    ?: throw IllegalStateException("hand frame could not be copied")
            input.width == handWidth && input.height == handHeight -> input
            else -> Bitmap.createScaledBitmap(input, handWidth, handHeight, true)
        }
        lastBitmapMs = android.os.SystemClock.elapsedRealtime() - bitmapStarted

        var handFuture: Future<HandPass>? = null
        try {
            val observed = mutableListOf<BoxObservation>()
            val wallStarted = android.os.SystemClock.elapsedRealtime()
            val runtime = handRuntime
            if (doHands && overlapModels && runtime != null) {
                handFuture = runtime.begin(handBitmap ?: input, width, height, onHands)
            }
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
                lastObjectMs = 0
                observed += recentObjects.map { it.copy(points = it.points.toList()) }
            }

            var optionalFailed = false
            var scannedOptional = false
            if (doHands && runtime != null) {
                scannedOptional = true
                if (handFuture == null) {
                    handFuture = runtime.begin(handBitmap ?: input, width, height, onHands)
                }
                val handPass = awaitHands(runtime, handFuture, {
                    runtime.begin(handBitmap ?: input, width, height, onHands)
                }) {
                    optionalFailed = true
                    lastOptionalFault = it
                }
                recentHands = handPass.boxes
                lastHandMs = handPass.modelMs
                handInferences += 1
            }
            lastWallMs = android.os.SystemClock.elapsedRealtime() - wallStarted
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
            handRuntime?.invalidateInFlight()
            handFuture?.cancel(true)
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

    private data class HandPass(val boxes: List<BoxObservation>, val modelMs: Long)

    private fun awaitHands(
        runtime: HandRuntime,
        started: Future<HandPass>,
        retry: () -> Future<HandPass>,
        onFault: (String) -> Unit,
    ): HandPass {
        val first = awaitOnce(runtime, started, onFault, allowFallback = true)
        if (first != null) return first
        if (!runtime.consumeRetry()) return HandPass(emptyList(), 0L)
        return awaitOnce(runtime, retry(), onFault, allowFallback = false)
            ?: HandPass(emptyList(), 0L)
    }

    private fun awaitOnce(
        runtime: HandRuntime,
        future: Future<HandPass>,
        onFault: (String) -> Unit,
        allowFallback: Boolean,
    ): HandPass? {
        return try {
            future.get(runtime.nextTimeout(), TimeUnit.MILLISECONDS)
        } catch (timeout: TimeoutException) {
            runtime.invalidateInFlight()
            future.cancel(true)
            if (allowFallback) runtime.fallbackToCpu() else onFault("hands: timed out")
            Log.w(TAG, "hand detect did not return", timeout)
            null
        } catch (error: Exception) {
            val cause = (error as? ExecutionException)?.cause ?: error
            runtime.invalidateInFlight()
            future.cancel(true)
            if (allowFallback && runtime.delegateName == "GPU") {
                Log.w(TAG, "GPU hand detect failed", cause)
                runtime.fallbackToCpu()
            } else {
                onFault("hands: " + (cause.message ?: cause.javaClass.simpleName))
                Log.e(TAG, "hands failed", cause)
            }
            null
        }
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

    private inner class HandRuntime(
        private val app: Context,
        private val numHands: Int,
        attemptGpu: Boolean,
    ) : AutoCloseable {
        var delegateName: String = "CPU"
            private set
        var attempt: String = "CPU"
            private set
        var fallbacks: Int = 0
            private set
        var abandonBitmap: Boolean = false
        @Volatile var lastModelMs: Long = 0L
        private var retryCpu = false
        private var marker: HandLandmarker? = null
        private var executor = newExecutor()
        private var gpuProbed = false
        private val ticket = AtomicInteger(0)

        init {
            if (!attemptGpu) {
                attempt = "CPU"
                delegateName = "CPU"
                marker = createOnExecutor(Delegate.CPU)
                gpuProbed = true
            } else {
                attempt = "GPU"
                val created = createOnExecutor(Delegate.GPU)
                if (created != null) {
                    marker = created
                    delegateName = "GPU"
                } else {
                    // The GPU create may still be inside native code on the old thread.
                    // Leave that delegate behind and build the CPU landmarker on a new thread.
                    replaceExecutor()
                    marker = createOnExecutor(Delegate.CPU)
                    delegateName = "CPU"
                    gpuProbed = true
                    fallbacks = 1
                    Log.i(TAG, "hand delegate fell back to CPU")
                }
            }
        }

        fun nextTimeout(): Long = if (!gpuProbed) PROBE_TIMEOUT_MS else DETECT_TIMEOUT_MS

        fun invalidateInFlight() {
            ticket.incrementAndGet()
        }

        fun begin(
            bitmap: Bitmap,
            frameWidth: Int,
            frameHeight: Int,
            onHands: ((List<BoxObservation>) -> Unit)?,
        ): Future<HandPass> {
            val mine = ticket.incrementAndGet()
            val owned = bitmap.copy(Bitmap.Config.ARGB_8888, false)
                ?: throw IllegalStateException("hand frame could not be copied")
            val current = marker
            if (current == null) {
                if (!owned.isRecycled) owned.recycle()
                return java.util.concurrent.CompletableFuture.completedFuture(HandPass(emptyList(), 0L))
            }
            return try {
                executor.submit<HandPass> {
                    if (ticket.get() != mine) {
                        if (!owned.isRecycled) owned.recycle()
                        return@submit HandPass(emptyList(), 0L)
                    }
                    val started = android.os.SystemClock.elapsedRealtime()
                    val image = BitmapImageBuilder(owned).build()
                    try {
                        val marked = current.detect(image)
                        val elapsed = android.os.SystemClock.elapsedRealtime() - started
                        val boxes = handsToBoxes(marked, frameWidth, frameHeight)
                        if (ticket.get() == mine) {
                            lastModelMs = elapsed
                            gpuProbed = true
                            try {
                                onHands?.invoke(boxes)
                            } catch (error: Exception) {
                                Log.w(TAG, "hand publish failed", error)
                            }
                        }
                        HandPass(boxes, elapsed)
                    } finally {
                        image.close()
                        if (!owned.isRecycled) owned.recycle()
                    }
                }
            } catch (error: Exception) {
                if (!owned.isRecycled) owned.recycle()
                throw error
            }
        }

        fun consumeRetry(): Boolean {
            if (!retryCpu) return false
            retryCpu = false
            return marker != null
        }

        fun fallbackToCpu() {
            if (delegateName == "CPU") return
            invalidateInFlight()
            delegateName = "CPU"
            gpuProbed = true
            fallbacks += 1
            replaceExecutor()
            marker = createOnExecutor(Delegate.CPU)
            retryCpu = marker != null
            Log.i(TAG, "hand delegate fell back to CPU")
        }

        private fun replaceExecutor() {
            val previous = executor
            executor = newExecutor()
            previous.shutdown()
        }

        private fun createOnExecutor(delegate: Delegate): HandLandmarker? {
            val future = try {
                executor.submit<HandLandmarker?> {
                    try {
                        create(delegate)
                    } catch (error: Exception) {
                        Log.w(TAG, "hand landmarker did not initialize", error)
                        null
                    }
                }
            } catch (error: Exception) {
                Log.w(TAG, "hand executor rejected create", error)
                return null
            }
            return try {
                future.get(CREATE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            } catch (error: Exception) {
                future.cancel(true)
                Log.w(TAG, "hand landmarker did not return", error)
                null
            }
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
                    .setNumHands(numHands)
                    .setMinHandDetectionConfidence(IndexPoint.MIN_SCORE)
                    .setMinHandPresenceConfidence(IndexPoint.MIN_SCORE)
                    .setMinTrackingConfidence(IndexPoint.MIN_SCORE)
                    .build()
            )

        override fun close() {
            invalidateInFlight()
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

        private fun newExecutor() = Executors.newSingleThreadExecutor { runnable ->
            Thread(runnable, "visor-hand").apply { isDaemon = true }
        }
    }

    companion object {
        private const val TAG = "VisorTracker"
        internal const val HAND_FRAME = 640
        /**
         * Production default. A same-image probe may record a faster count, and the
         * constant changes only when that probe still finds the extended index.
         */
        internal const val HAND_COUNT = 2
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
