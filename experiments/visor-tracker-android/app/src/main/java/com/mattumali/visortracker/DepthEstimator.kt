package com.mattumali.visortracker

import android.content.Context
import android.content.res.AssetFileDescriptor
import android.graphics.Bitmap
import android.graphics.Color
import android.util.Log
import org.tensorflow.lite.Interpreter
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.MappedByteBuffer
import java.nio.channels.FileChannel
import kotlin.math.max
import kotlin.math.min

/**
 * Depth estimation engine supporting:
 * 1. Depth Anything V2 Small (Qualcomm optimized, relative depth)
 * 2. YOLO26-Depth (alternative metric depth backend)
 *
 * Provides aligned depth evidence for 3D pointing geometry and target disambiguation.
 */
class DepthEstimator(
    private val context: Context,
    var activeBackend: DepthBackend = DepthBackend.DEPTH_ANYTHING_V2,
) : AutoCloseable {

    enum class DepthBackend {
        DEPTH_ANYTHING_V2,
        YOLO26_DEPTH,
    }

    data class DepthMap(
        val width: Int,
        val height: Int,
        val data: FloatArray,
        val minDepth: Float,
        val maxDepth: Float,
        val backend: DepthBackend,
        val inferenceMs: Long,
    ) {
        /**
         * Returns normalized relative depth [0.0..1.0] at normalized coordinates (0.0..1.0).
         */
        fun getDepthAt(normX: Float, normY: Float): Float {
            val clampedX = max(0f, min(1f, normX))
            val clampedY = max(0f, min(1f, normY))
            val px = (clampedX * (width - 1)).toInt()
            val py = (clampedY * (height - 1)).toInt()
            val idx = py * width + px
            return if (idx in data.indices) data[idx] else 0.5f
        }

        /**
         * Computes the median depth within a bounding box.
         */
        fun getMedianDepthInBox(
            left: Float,
            top: Float,
            right: Float,
            bottom: Float,
            frameWidth: Int,
            frameHeight: Int,
        ): Float {
            if (frameWidth <= 0 || frameHeight <= 0) return 0.5f
            val normLeft = max(0f, min(1f, left / frameWidth))
            val normTop = max(0f, min(1f, top / frameHeight))
            val normRight = max(0f, min(1f, right / frameWidth))
            val normBottom = max(0f, min(1f, bottom / frameHeight))

            val x0 = (normLeft * (width - 1)).toInt()
            val y0 = (normTop * (height - 1)).toInt()
            val x1 = (normRight * (width - 1)).toInt()
            val y1 = (normBottom * (height - 1)).toInt()

            val samples = mutableListOf<Float>()
            val stepX = max(1, (x1 - x0) / 10)
            val stepY = max(1, (y1 - y0) / 10)

            for (y in y0..y1 step stepY) {
                for (x in x0..x1 step stepX) {
                    val idx = y * width + x
                    if (idx in data.indices) {
                        samples.add(data[idx])
                    }
                }
            }

            if (samples.isEmpty()) return getDepthAt((normLeft + normRight) / 2f, (normTop + normBottom) / 2f)
            samples.sort()
            return samples[samples.size / 2]
        }
    }

    private var dav2Interpreter: Interpreter? = null
    private var yoloDepthInterpreter: Interpreter? = null

    init {
        initInterpreters()
    }

    private fun initInterpreters() {
        val options = Interpreter.Options().apply {
            setNumThreads(4)
        }
        try {
            val dav2Buffer = loadModelFile(context, "models/depth_anything_v2_small.tflite")
            if (dav2Buffer != null) {
                dav2Interpreter = Interpreter(dav2Buffer, options)
                Log.i(TAG, "Loaded Depth Anything V2 Small interpreter")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to load Depth Anything V2: ${e.message}")
        }

        try {
            val yoloDepthBuffer = loadModelFile(context, "models/yolo26n-depth.tflite")
            if (yoloDepthBuffer != null) {
                yoloDepthInterpreter = Interpreter(yoloDepthBuffer, options)
                Log.i(TAG, "Loaded YOLO26-Depth interpreter")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to load YOLO26-Depth: ${e.message}")
        }
    }

    /**
     * Estimates depth for the given input frame.
     */
    fun estimate(bitmap: Bitmap): DepthMap? {
        val startedAt = System.currentTimeMillis()
        return when (activeBackend) {
            DepthBackend.DEPTH_ANYTHING_V2 -> {
                val interp = dav2Interpreter ?: yoloDepthInterpreter ?: return null
                estimateWithDav2(interp, bitmap, startedAt)
            }
            DepthBackend.YOLO26_DEPTH -> {
                val interp = yoloDepthInterpreter ?: dav2Interpreter ?: return null
                estimateWithYoloDepth(interp, bitmap, startedAt)
            }
        }
    }

    private fun estimateWithDav2(interpreter: Interpreter, bitmap: Bitmap, startedAt: Long): DepthMap {
        val inputSize = 518
        val scaled = Bitmap.createScaledBitmap(bitmap, inputSize, inputSize, true)
        val byteBuffer = ByteBuffer.allocateDirect(1 * inputSize * inputSize * 3 * 4).apply {
            order(ByteOrder.nativeOrder())
        }

        val pixels = IntArray(inputSize * inputSize)
        scaled.getPixels(pixels, 0, inputSize, 0, 0, inputSize, inputSize)
        for (pixel in pixels) {
            val r = (Color.red(pixel) / 255.0f)
            val g = (Color.green(pixel) / 255.0f)
            val b = (Color.blue(pixel) / 255.0f)
            byteBuffer.putFloat(r)
            byteBuffer.putFloat(g)
            byteBuffer.putFloat(b)
        }
        byteBuffer.rewind()

        val outputArray = Array(1) { Array(inputSize) { Array(inputSize) { FloatArray(1) } } }
        interpreter.run(byteBuffer, outputArray)

        val depthArray = FloatArray(inputSize * inputSize)
        var minVal = Float.MAX_VALUE
        var maxVal = -Float.MAX_VALUE

        var idx = 0
        for (y in 0 until inputSize) {
            for (x in 0 until inputSize) {
                val v = outputArray[0][y][x][0]
                depthArray[idx++] = v
                if (v < minVal) minVal = v
                if (v > maxVal) maxVal = v
            }
        }

        // Normalize relative depth into [0.0..1.0] where 0.0 is nearest and 1.0 is farthest
        val range = if (maxVal > minVal) maxVal - minVal else 1.0f
        for (i in depthArray.indices) {
            depthArray[i] = (depthArray[i] - minVal) / range
        }

        val latency = System.currentTimeMillis() - startedAt
        return DepthMap(
            width = inputSize,
            height = inputSize,
            data = depthArray,
            minDepth = minVal,
            maxDepth = maxVal,
            backend = DepthBackend.DEPTH_ANYTHING_V2,
            inferenceMs = latency,
        )
    }

    private fun estimateWithYoloDepth(interpreter: Interpreter, bitmap: Bitmap, startedAt: Long): DepthMap {
        val inputSize = 768
        val scaled = Bitmap.createScaledBitmap(bitmap, inputSize, inputSize, true)
        val byteBuffer = ByteBuffer.allocateDirect(1 * inputSize * inputSize * 3 * 4).apply {
            order(ByteOrder.nativeOrder())
        }

        val pixels = IntArray(inputSize * inputSize)
        scaled.getPixels(pixels, 0, inputSize, 0, 0, inputSize, inputSize)
        for (pixel in pixels) {
            byteBuffer.putFloat(Color.red(pixel) / 255.0f)
            byteBuffer.putFloat(Color.green(pixel) / 255.0f)
            byteBuffer.putFloat(Color.blue(pixel) / 255.0f)
        }
        byteBuffer.rewind()

        // YOLO26-depth output shape
        val outTensor = interpreter.getOutputTensor(0)
        val shape = outTensor.shape()
        val outH = if (shape.size > 1 && shape[1] > 1) shape[1] else 192
        val outW = if (shape.size > 2 && shape[2] > 1) shape[2] else 192

        val outputArray = Array(1) { Array(outH) { Array(outW) { FloatArray(1) } } }
        interpreter.run(byteBuffer, outputArray)

        val depthArray = FloatArray(outW * outH)
        var minVal = Float.MAX_VALUE
        var maxVal = -Float.MAX_VALUE

        var idx = 0
        for (y in 0 until outH) {
            for (x in 0 until outW) {
                val v = outputArray[0][y][x][0]
                depthArray[idx++] = v
                if (v < minVal) minVal = v
                if (v > maxVal) maxVal = v
            }
        }

        val latency = System.currentTimeMillis() - startedAt
        return DepthMap(
            width = outW,
            height = outH,
            data = depthArray,
            minDepth = minVal,
            maxDepth = maxVal,
            backend = DepthBackend.YOLO26_DEPTH,
            inferenceMs = latency,
        )
    }

    override fun close() {
        dav2Interpreter?.close()
        yoloDepthInterpreter?.close()
    }

    companion object {
        private const val TAG = "DepthEstimator"

        fun loadModelFile(context: Context, modelPath: String): MappedByteBuffer? {
            return try {
                val fileDescriptor: AssetFileDescriptor = context.assets.openFd(modelPath)
                val inputStream = FileInputStream(fileDescriptor.fileDescriptor)
                val fileChannel: FileChannel = inputStream.channel
                val startOffset: Long = fileDescriptor.startOffset
                val declaredLength: Long = fileDescriptor.declaredLength
                fileChannel.map(FileChannel.MapMode.READ_ONLY, startOffset, declaredLength)
            } catch (e: Exception) {
                Log.e(TAG, "Error opening asset $modelPath: ${e.message}")
                null
            }
        }
    }
}
