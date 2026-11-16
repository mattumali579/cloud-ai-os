package com.mattumali.visortracker

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Point
import android.graphics.Rect
import android.util.Log
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.facedetector.FaceDetector
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarker
import com.google.mediapipe.tasks.vision.objectdetector.ObjectDetector
import org.opencv.android.OpenCVLoader
import org.opencv.android.Utils
import org.opencv.core.Mat
import org.opencv.core.Point as CvPoint
import org.opencv.core.Rect as CvRect
import org.opencv.core.Scalar
import org.opencv.core.Size
import org.opencv.imgproc.Imgproc
import kotlin.math.max
import kotlin.math.min

class VisionPipeline(context: Context) : AutoCloseable {
    private val appContext = context.applicationContext
    private val tracker = TrackIds()
    private val hands: HandLandmarker
    private val faces: FaceDetector
    private val objects: ObjectDetector

    init {
        check(OpenCVLoader.initLocal()) { "OpenCV native library could not load" }
        hands = HandLandmarker.createFromOptions(
            appContext,
            HandLandmarker.HandLandmarkerOptions.builder()
                .setBaseOptions(BaseOptions.builder().setModelAssetPath("hand_landmarker.task").build())
                .setRunningMode(RunningMode.IMAGE)
                .setNumHands(2)
                .setMinHandDetectionConfidence(0.45f)
                .setMinHandPresenceConfidence(0.45f)
                .setMinTrackingConfidence(0.45f)
                .build()
        )
        faces = FaceDetector.createFromOptions(
            appContext,
            FaceDetector.FaceDetectorOptions.builder()
                .setBaseOptions(BaseOptions.builder().setModelAssetPath("blaze_face_full_range.tflite").build())
                .setRunningMode(RunningMode.IMAGE)
                .setMinDetectionConfidence(0.5f)
                .build()
        )
        objects = ObjectDetector.createFromOptions(
            appContext,
            ObjectDetector.ObjectDetectorOptions.builder()
                .setBaseOptions(BaseOptions.builder().setModelAssetPath("efficientdet_lite0.tflite").build())
                .setRunningMode(RunningMode.IMAGE)
                .setMaxResults(8)
                .setScoreThreshold(0.35f)
                .build()
        )
    }

    fun analyze(input: Bitmap): Bitmap {
        val rgba = Mat()
        val rgb = Mat()
        try {
            Utils.bitmapToMat(input, rgba)
            val scale = min(1f, 640f / input.width)
            val width = max(1, (input.width * scale).toInt())
            val height = max(1, (input.height * scale).toInt())
            if (width != input.width || height != input.height) {
                Imgproc.resize(rgba, rgba, Size(width.toDouble(), height.toDouble()))
            }
            Imgproc.cvtColor(rgba, rgb, Imgproc.COLOR_RGBA2RGB)

            val modelBitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
            Utils.matToBitmap(rgba, modelBitmap)
            val image = BitmapImageBuilder(modelBitmap).build()
            val detections = mutableListOf<BoxObservation>()

            hands.detect(image).landmarks().forEach { hand ->
                if (hand.isNotEmpty()) {
                    val points = hand.map { it.x() * width to it.y() * height }
                    val xs = points.map { it.first }
                    val ys = points.map { it.second }
                    detections += BoxObservation(
                        kind = "hand", label = "hand", score = 1f,
                        left = xs.minOrNull() ?: 0f, top = ys.minOrNull() ?: 0f,
                        right = xs.maxOrNull() ?: 0f, bottom = ys.maxOrNull() ?: 0f,
                        points = points
                    )
                }
            }

            faces.detect(image).detections().forEach { detection ->
                val box = detection.boundingBox()
                val score = detection.categories().firstOrNull()?.score() ?: 0f
                detections += box.toObservation("face", "face", score)
            }

            objects.detect(image).detections().forEach { detection ->
                val category = detection.categories().maxByOrNull { it.score() }
                if (category != null) {
                    detections += detection.boundingBox().toObservation(
                        "object",
                        category.categoryName().ifBlank { "object" },
                        category.score()
                    )
                }
            }

            val tracked = tracker.update(detections)
            tracked.forEach { detection ->
                val rect = CvRect(
                    detection.left.toInt().coerceIn(0, width - 1),
                    detection.top.toInt().coerceIn(0, height - 1),
                    max(1, (detection.right - detection.left).toInt()).coerceAtMost(width),
                    max(1, (detection.bottom - detection.top).toInt()).coerceAtMost(height)
                )
                Imgproc.rectangle(rgb, rect, colorFor(detection.kind), 2)
                val caption = "${detection.kind} #${detection.trackId} ${detection.label} ${(detection.score * 100).toInt()}%"
                Imgproc.putText(
                    rgb, caption,
                    CvPoint(rect.x.toDouble(), max(18, rect.y - 5).toDouble()),
                    Imgproc.FONT_HERSHEY_SIMPLEX, 0.42, colorFor(detection.kind), 1
                )
                detection.points.forEach { (x, y) ->
                    Imgproc.circle(rgb, CvPoint(x.toDouble(), y.toDouble()), 3, Scalar(255.0, 255.0, 0.0), -1)
                }
            }

            val output = Mat()
            try {
                Imgproc.cvtColor(rgb, output, Imgproc.COLOR_RGB2RGBA)
                val result = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
                Utils.matToBitmap(output, result)
                return result
            } finally {
                output.release()
                modelBitmap.recycle()
            }
        } catch (error: Exception) {
            Log.e(TAG, "Frame analysis failed", error)
            throw error
        } finally {
            rgba.release()
            rgb.release()
        }
    }

    private fun Rect.toObservation(kind: String, label: String, score: Float) =
        BoxObservation(kind, label, score, left.toFloat(), top.toFloat(), right.toFloat(), bottom.toFloat())

    private fun colorFor(kind: String): Scalar = when (kind) {
        "face" -> Scalar(0.0, 255.0, 255.0)
        "hand" -> Scalar(255.0, 200.0, 0.0)
        else -> Scalar(0.0, 255.0, 0.0)
    }

    override fun close() {
        hands.close()
        faces.close()
        objects.close()
    }

    companion object { private const val TAG = "VisorTracker" }
}
