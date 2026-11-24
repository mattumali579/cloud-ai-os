package com.mattumali.visortracker

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import android.util.Log
import java.io.File
import java.nio.FloatBuffer
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min

/**
 * YOLO26n-seg instance segmentation and object detection engine for Android powered by ONNX Runtime.
 *
 * Implements:
 * 1. Bounding box detection across 80 COCO classes.
 * 2. 32-prototype instance segmentation mask reconstruction.
 * 3. Non-Maximum Suppression (NMS) filtering.
 * 4. Integration into TrackIds and SpatialTargetingEngine via BoxObservation.points.
 */
class Yolo26Segmentation(
    private val context: Context? = null,
    private val modelBytes: ByteArray? = null,
    private val modelPath: String? = null,
    private val scoreThreshold: Float = 0.25f,
    private val iouThreshold: Float = 0.45f,
    private val maxResults: Int = 8,
) : AutoCloseable {

    data class DetectionResult(
        val boxes: List<BoxObservation>,
        val inferenceMs: Long,
        val modelUsed: String,
    )

    private var env: OrtEnvironment? = null
    private var session: OrtSession? = null
    private var inputName: String = "images"
    private var modelAssetPath: String = "yolo26n-seg.onnx"
    val isInitialized: Boolean get() = session != null

    init {
        initModel()
    }

    private fun initModel() {
        try {
            val ortEnv = OrtEnvironment.getEnvironment()
            env = ortEnv
            val sessionOptions = OrtSession.SessionOptions().apply {
                setIntraOpNumThreads(4)
                setInterOpNumThreads(4)
                try {
                    addNnapi()
                } catch (ignored: Throwable) {
                    // NNAPI not available on host JVM or unsupported device
                }
            }

            var loadedSession: OrtSession? = null

            // 1. Try explicit modelBytes
            if (modelBytes != null && modelBytes.isNotEmpty()) {
                loadedSession = ortEnv.createSession(modelBytes, sessionOptions)
                modelAssetPath = "in-memory-bytes"
                Log.i(TAG, "Initialized YOLO26n-seg from byte array (${modelBytes.size} bytes)")
            }

            // 2. Try explicit modelPath
            if (loadedSession == null && modelPath != null) {
                val file = File(modelPath)
                if (file.exists() && file.length() > 0L) {
                    loadedSession = ortEnv.createSession(file.absolutePath, sessionOptions)
                    modelAssetPath = file.name
                    Log.i(TAG, "Initialized YOLO26n-seg from file: ${file.absolutePath}")
                }
            }

            // 3. Try Android assets if context available
            if (loadedSession == null && context != null) {
                val candidateAssets = listOf(
                    "yoloe-26n-seg.onnx",
                    "models/yoloe-26n-seg.onnx",
                    "yolo26n-seg.onnx",
                    "models/yolo26n-seg.onnx",
                    "generated/visionAssets/yolo26n-seg.onnx"
                )
                for (asset in candidateAssets) {
                    try {
                        val bytes = context.assets.open(asset).use { it.readBytes() }
                        if (bytes.isNotEmpty()) {
                            loadedSession = ortEnv.createSession(bytes, sessionOptions)
                            modelAssetPath = asset
                            Log.i(TAG, "Initialized YOLO26n-seg from asset: $asset (${bytes.size} bytes)")
                            break
                        }
                    } catch (ignored: Exception) {
                        // try next candidate
                    }
                }
            }

            // 4. Try standard filesystem paths (host tests & local development)
            if (loadedSession == null) {
                val candidatePaths = listOf(
                    "yoloe-26n-seg.onnx",
                    "../yoloe-26n-seg.onnx",
                    "../../yoloe-26n-seg.onnx",
                    "C:/Users/Admin/cloud-ai-os-visor-tracker/yoloe-26n-seg.onnx",
                    "C:/Users/Admin/cloud-ai-os-visor-tracker/experiments/visor-tracker-android/app/src/main/assets/yoloe-26n-seg.onnx",
                    "C:/Users/Admin/cloud-ai-os-visor-tracker/experiments/visor-tracker-android/app/src/main/assets/models/yoloe-26n-seg.onnx",
                    "yolo26n-seg.onnx",
                    "../yolo26n-seg.onnx",
                    "../../yolo26n-seg.onnx",
                    "C:/Users/Admin/cloud-ai-os-visor-tracker/yolo26n-seg.onnx",
                    "C:/Users/Admin/cloud-ai-os-visor-tracker/experiments/visor-tracker-android/app/src/main/assets/models/yolo26n-seg.onnx",
                    "C:/Users/Admin/cloud-ai-os-visor-tracker/experiments/visor-tracker-android/app/src/main/assets/yolo26n-seg.onnx",
                    "C:/Users/Admin/cloud-ai-os-visor-tracker/experiments/visor-tracker-android/app/build/generated/visionAssets/yolo26n-seg.onnx"
                )
                for (path in candidatePaths) {
                    val file = File(path)
                    if (file.exists() && file.length() > 0L) {
                        try {
                            loadedSession = ortEnv.createSession(file.absolutePath, sessionOptions)
                            modelAssetPath = file.name
                            Log.i(TAG, "Initialized YOLO26n-seg from fallback file: ${file.absolutePath}")
                            break
                        } catch (e: Exception) {
                            Log.w(TAG, "Failed to load ONNX from ${file.absolutePath}: ${e.message}")
                        }
                    }
                }
            }

            if (loadedSession != null) {
                session = loadedSession
                inputName = loadedSession.inputNames.iterator().next()
                Log.i(TAG, "YOLO26n-seg ready. Input: $inputName, Outputs: ${loadedSession.outputNames}")
            } else {
                Log.w(TAG, "Could not find yolo26n-seg.onnx model asset or file")
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error initializing YOLO26n-seg ONNX runtime: ${e.message}", e)
        }
    }

    /**
     * Executes genuine YOLO26n-seg inference on the provided frame bitmap.
     */
    fun detect(bitmap: Bitmap): DetectionResult {
        val origW = bitmap.width
        val origH = bitmap.height
        val pixels = IntArray(origW * origH)
        try {
            bitmap.getPixels(pixels, 0, origW, 0, 0, origW, origH)
        } catch (ignored: Throwable) {
            // In unit tests where Bitmap methods are stubbed
        }
        return detectPixels(pixels, origW, origH)
    }

    /**
     * Executes genuine YOLO26n-seg inference on raw ARGB pixel data.
     */
    fun detectPixels(pixels: IntArray, origW: Int, origH: Int): DetectionResult {
        val startedAt = System.currentTimeMillis()
        val sess = session
        val ortEnv = env
        if (sess == null || ortEnv == null) {
            return DetectionResult(emptyList(), 0L, "uninitialized")
        }

        val inputSize = 640
        val totalPixels = inputSize * inputSize

        // Ultralytics models are trained/exported with aspect-preserving
        // letterboxing. Stretching a 1080x1440 camera frame to a square moves
        // boxes and masks relative to MediaPipe landmarks.
        val transform = LetterboxTransform.forFrame(origW, origH, inputSize)
        val scaledPixels = if (origW == inputSize && origH == inputSize && pixels.size == totalPixels) {
            pixels
        } else if (pixels.isNotEmpty() && origW > 0 && origH > 0) {
            val out = IntArray(totalPixels) { 0xFF727272.toInt() }
            for (y in 0 until transform.scaledHeight) {
                val srcY = (y / transform.scale).toInt().coerceIn(0, origH - 1)
                val srcRow = srcY * origW
                val dstRow = (y + transform.padY) * inputSize
                for (x in 0 until transform.scaledWidth) {
                    val srcX = (x / transform.scale).toInt().coerceIn(0, origW - 1)
                    out[dstRow + x + transform.padX] = pixels[srcRow + srcX]
                }
            }
            out
        } else {
            IntArray(totalPixels)
        }

        // Convert ARGB IntArray to NCHW normalized float array: [1, 3, 640, 640]
        val floatArray = FloatArray(3 * totalPixels)
        val rOffset = 0
        val gOffset = totalPixels
        val bOffset = 2 * totalPixels

        for (i in 0 until totalPixels) {
            val p = scaledPixels[i]
            floatArray[rOffset + i] = (((p shr 16) and 0xFF)) / 255.0f
            floatArray[gOffset + i] = (((p shr 8) and 0xFF)) / 255.0f
            floatArray[bOffset + i] = ((p and 0xFF)) / 255.0f
        }

        val floatBuffer = FloatBuffer.wrap(floatArray)
        val inputTensor = OnnxTensor.createTensor(ortEnv, floatBuffer, longArrayOf(1, 3, 640, 640))

        try {
            // 2. Run inference: outputs are output0 [1, 116, 8400] and output1 [1, 32, 160, 160]
            val results = sess.run(mapOf(inputName to inputTensor))
            try {
                val out0 = results.get(0) as OnnxTensor
                val out1 = results.get(1) as OnnxTensor

                @Suppress("UNCHECKED_CAST")
                val out0Value = out0.value as Array<Array<FloatArray>>
                @Suppress("UNCHECKED_CAST")
                val out1Value = out1.value as Array<Array<Array<FloatArray>>>

                val boxes = parseSegmentationOutputs(out0Value[0], out1Value[0], origW, origH, transform)
                val latency = System.currentTimeMillis() - startedAt
                return DetectionResult(boxes, latency, modelAssetPath)
            } finally {
                results.close()
            }
        } catch (e: Exception) {
            Log.e(TAG, "YOLO26 inference error: ${e.message}", e)
            val latency = System.currentTimeMillis() - startedAt
            return DetectionResult(emptyList(), latency, modelAssetPath)
        } finally {
            inputTensor.close()
        }
    }

    private fun parseSegmentationOutputs(
        out0: Array<FloatArray>, // [channels][8400]
        out1: Array<Array<FloatArray>>, // [32][160][160]
        origW: Int,
        origH: Int,
        transform: LetterboxTransform = LetterboxTransform.forFrame(origW, origH, 640),
    ): List<BoxObservation> {
        val rawDetections = mutableListOf<RawBox>()
        val totalChannels = out0.size
        val numMaskCoeffs = 32
        val numClasses = totalChannels - 4 - numMaskCoeffs
        val classLabels = when (numClasses) {
            8 -> YOLOE_CLASSES
            12 -> YOLOE_CLASSES_12
            80 -> COCO_CLASSES
            else -> if (numClasses <= COCO_CLASSES.size) COCO_CLASSES.subList(0, numClasses) else List(numClasses) { "class_$it" }
        }

        // 8400 detection anchors
        for (i in 0 until 8400) {
            var maxScore = 0f
            var bestClass = -1
            for (c in 0 until numClasses) {
                val score = out0[4 + c][i]
                if (score > maxScore) {
                    maxScore = score
                    bestClass = c
                }
            }

            if (maxScore >= scoreThreshold && bestClass in classLabels.indices) {
                val cx = out0[0][i]
                val cy = out0[1][i]
                val w = out0[2][i]
                val h = out0[3][i]

                val left = transform.toFrameX(cx - w / 2f, origW)
                val top = transform.toFrameY(cy - h / 2f, origH)
                val right = transform.toFrameX(cx + w / 2f, origW)
                val bottom = transform.toFrameY(cy + h / 2f, origH)

                // Channels (4 + numClasses)..(4 + numClasses + 31): 32 mask coefficients
                val maskCoeffs = FloatArray(numMaskCoeffs) { k -> out0[4 + numClasses + k][i] }
                rawDetections.add(RawBox(bestClass, maxScore, left, top, right, bottom, maskCoeffs))
            }
        }

        // Apply Non-Maximum Suppression (NMS)
        val nmsSurvived = applyNms(rawDetections, iouThreshold)
        val capped = if (nmsSurvived.size > maxResults) nmsSurvived.subList(0, maxResults) else nmsSurvived

        // Reconstruct instance segmentation masks
        val observations = mutableListOf<BoxObservation>()
        for (raw in capped) {
            val label = classLabels[raw.classId]
            val maskPoints = extractMaskContour(raw, out1, origW, origH, transform)
            observations.add(
                BoxObservation(
                    kind = "object",
                    label = label,
                    score = raw.score,
                    left = raw.left,
                    top = raw.top,
                    right = raw.right,
                    bottom = raw.bottom,
                    points = maskPoints,
                )
            )
        }
        return observations
    }

    private fun extractMaskContour(
        raw: RawBox,
        protoMasks: Array<Array<FloatArray>>, // [32][160][160]
        origW: Int,
        origH: Int,
        transform: LetterboxTransform,
    ): List<Pair<Float, Float>> {
        if (raw.maskCoeffs.isEmpty()) return emptyList()

        val px0 = max(0, ((raw.left * transform.scale + transform.padX) / 4f).toInt())
        val py0 = max(0, ((raw.top * transform.scale + transform.padY) / 4f).toInt())
        val px1 = min(159, ((raw.right * transform.scale + transform.padX) / 4f).toInt())
        val py1 = min(159, ((raw.bottom * transform.scale + transform.padY) / 4f).toInt())

        if (px1 <= px0 || py1 <= py0) return emptyList()

        val stepX = max(1, (px1 - px0) / 20)
        val stepY = max(1, (py1 - py0) / 20)
        val gridW = ((px1 - px0) / stepX) + 1
        val gridH = ((py1 - py0) / stepY) + 1
        val maskGrid = Array(gridH) { BooleanArray(gridW) }

        var count = 0
        var sumX = 0f
        var sumY = 0f

        for (gy in 0 until gridH) {
            val py = min(159, py0 + gy * stepY)
            for (gx in 0 until gridW) {
                val px = min(159, px0 + gx * stepX)
                var dot = 0f
                for (k in 0 until 32) {
                    dot += raw.maskCoeffs[k] * protoMasks[k][py][px]
                }
                val isOccupied = (1f / (1f + exp(-dot))) > 0.5f
                maskGrid[gy][gx] = isOccupied
                if (isOccupied) {
                    val fx = transform.toFrameX(px * 4f, origW)
                    val fy = transform.toFrameY(py * 4f, origH)
                    sumX += fx
                    sumY += fy
                    count++
                }
            }
        }
        if (count < 3) return emptyList()

        val cx = sumX / count
        val cy = sumY / count

        // Extract real boundary points (cells with at least one 4-neighbor that is unoccupied).
        // Order boundary points radially around mask centroid to preserve concavities
        // without false convex hull bridging across gaps (Failure Mode 11).
        val boundaryPoints = mutableListOf<Pair<Float, Float>>()
        for (gy in 0 until gridH) {
            val py = min(159, py0 + gy * stepY)
            for (gx in 0 until gridW) {
                if (!maskGrid[gy][gx]) continue
                val isBoundary = gx == 0 || gx == gridW - 1 || gy == 0 || gy == gridH - 1 ||
                    !maskGrid[gy - 1][gx] || !maskGrid[gy + 1][gx] ||
                    !maskGrid[gy][gx - 1] || !maskGrid[gy][gx + 1]
                if (isBoundary) {
                    val px = min(159, px0 + gx * stepX)
                    val fx = transform.toFrameX(px * 4f, origW)
                    val fy = transform.toFrameY(py * 4f, origH)
                    boundaryPoints.add(fx to fy)
                }
            }
        }

        if (boundaryPoints.size < 3) return emptyList()

        return boundaryPoints.sortedBy { p ->
            kotlin.math.atan2(p.second - cy, p.first - cx)
        }
    }

    data class LetterboxTransform(
        val scale: Float,
        val padX: Int,
        val padY: Int,
        val scaledWidth: Int,
        val scaledHeight: Int,
    ) {
        fun toFrameX(modelX: Float, frameWidth: Int): Float =
            ((modelX - padX) / scale).coerceIn(0f, frameWidth.toFloat())

        fun toFrameY(modelY: Float, frameHeight: Int): Float =
            ((modelY - padY) / scale).coerceIn(0f, frameHeight.toFloat())

        companion object {
            fun forFrame(width: Int, height: Int, inputSize: Int): LetterboxTransform {
                if (width <= 0 || height <= 0) return LetterboxTransform(1f, 0, 0, inputSize, inputSize)
                val scale = min(inputSize.toFloat() / width, inputSize.toFloat() / height)
                val scaledWidth = (width * scale).toInt().coerceIn(1, inputSize)
                val scaledHeight = (height * scale).toInt().coerceIn(1, inputSize)
                val padX = (inputSize - scaledWidth) / 2
                val padY = (inputSize - scaledHeight) / 2
                return LetterboxTransform(scale, padX, padY, scaledWidth, scaledHeight)
            }
        }
    }

    private data class RawBox(
        val classId: Int,
        val score: Float,
        val left: Float,
        val top: Float,
        val right: Float,
        val bottom: Float,
        val maskCoeffs: FloatArray,
    ) {
        fun area(): Float = max(0f, right - left) * max(0f, bottom - top)
    }

    private fun applyNms(boxes: List<RawBox>, threshold: Float): List<RawBox> {
        val sorted = boxes.sortedByDescending { it.score }
        val selected = mutableListOf<RawBox>()
        for (box in sorted) {
            var overlap = false
            for (chosen in selected) {
                val boxIou = iou(box, chosen)
                // Class-aware NMS: suppress overlapping boxes of the same class (threshold ~0.45)
                // and cross-class duplicate detections of the exact same physical object (IoU > 0.60)
                if (box.classId == chosen.classId && boxIou > threshold) {
                    overlap = true
                    break
                }
                if (box.classId != chosen.classId && boxIou > 0.60f) {
                    overlap = true
                    break
                }
            }
            if (!overlap) {
                selected.add(box)
            }
        }
        return selected
    }

    private fun iou(a: RawBox, b: RawBox): Float {
        val interL = max(a.left, b.left)
        val interT = max(a.top, b.top)
        val interR = min(a.right, b.right)
        val interB = min(a.bottom, b.bottom)
        val interArea = max(0f, interR - interL) * max(0f, interB - interT)
        val unionArea = a.area() + b.area() - interArea
        return if (unionArea > 0f) interArea / unionArea else 0f
    }

    override fun close() {
        session?.close()
        session = null
        env?.close()
        env = null
    }

    companion object {
        private const val TAG = "Yolo26Segmentation"

        val YOLOE_CLASSES = listOf(
            "clothes hamper", "hanging clothes", "portable air conditioner",
            "laptop", "white plastic trash bag", "mouse", "bottle", "person"
        )

        val YOLOE_CLASSES_12 = listOf(
            "laundry basket", "closet clothing", "portable air conditioner",
            "windowsill", "conical hat", "laptop", "paper roll",
            "white plastic-covered item", "trash bag", "mouse", "bottle", "person"
        )

        val COCO_CLASSES = listOf(
            "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
            "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
            "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
            "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
            "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
            "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
            "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
            "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
            "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator",
            "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"
        )
    }
}
