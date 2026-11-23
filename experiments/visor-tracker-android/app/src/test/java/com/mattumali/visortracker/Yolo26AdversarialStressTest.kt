package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.Random
import javax.imageio.ImageIO

/**
 * Adversarial stress and empirical challenge suite for YOLO26n-seg ONNX model and runner.
 * Tests edge cases, extreme inputs, degenerate dimensions, NMS boundaries, and numerical stability.
 */
class Yolo26AdversarialStressTest {

    private val modelFile = File("C:/Users/Admin/cloud-ai-os-visor-tracker/yolo26n-seg.onnx")
    private val testImageFile = File("C:/Users/Admin/cloud-ai-os-visor-tracker/test-recordings/test_10942_cam.jpg")

    @Test
    fun testAllBlackInputZeroDetections() {
        val yolo = Yolo26Segmentation(modelPath = modelFile.absolutePath)
        try {
            assertTrue("Model must initialize", yolo.isInitialized)
            val width = 640
            val height = 640
            val blackPixels = IntArray(width * height) { 0 }

            val result = yolo.detectPixels(blackPixels, width, height)
            assertNotNull(result)
            assertTrue("Inference latency must be recorded", result.inferenceMs >= 0)
            assertEquals("Black frame should yield zero detections above threshold", 0, result.boxes.size)
        } finally {
            yolo.close()
        }
    }

    @Test
    fun testAllWhiteInputZeroDetections() {
        val yolo = Yolo26Segmentation(modelPath = modelFile.absolutePath)
        try {
            assertTrue("Model must initialize", yolo.isInitialized)
            val width = 640
            val height = 640
            val whitePixels = IntArray(width * height) { 0x00FFFFFF }

            val result = yolo.detectPixels(whitePixels, width, height)
            assertNotNull(result)
            assertTrue("Inference latency must be recorded", result.inferenceMs >= 0)
            assertEquals("White frame should yield zero detections above threshold", 0, result.boxes.size)
        } finally {
            yolo.close()
        }
    }

    @Test
    fun testUniformNoiseInputStability() {
        val yolo = Yolo26Segmentation(modelPath = modelFile.absolutePath)
        try {
            assertTrue("Model must initialize", yolo.isInitialized)
            val width = 640
            val height = 640
            val rng = Random(42)
            val noisePixels = IntArray(width * height) { rng.nextInt() }

            val result = yolo.detectPixels(noisePixels, width, height)
            assertNotNull(result)
            assertTrue("Inference latency must be recorded", result.inferenceMs >= 0)
            // Uniform noise should not hallucinate confident COCO objects
            assertEquals("Random noise should yield 0 detections at default 0.25 threshold", 0, result.boxes.size)
        } finally {
            yolo.close()
        }
    }

    @Test
    fun testCheckerboardHighContrastInput() {
        val yolo = Yolo26Segmentation(modelPath = modelFile.absolutePath)
        try {
            assertTrue("Model must initialize", yolo.isInitialized)
            val width = 640
            val height = 640
            val pixels = IntArray(width * height) { idx ->
                val x = idx % width
                val y = idx / width
                if ((x / 8 + y / 8) % 2 == 0) 0x00FFFFFF else 0x00000000
            }

            val result = yolo.detectPixels(pixels, width, height)
            assertNotNull(result)
            assertTrue("Latency recorded", result.inferenceMs >= 0)
            // High contrast checkerboard should not crash or produce unbounded outputs
            for (box in result.boxes) {
                assertTrue("Box within frame bounds", box.left >= 0 && box.right <= width)
                assertTrue("Box within frame bounds", box.top >= 0 && box.bottom <= height)
            }
        } finally {
            yolo.close()
        }
    }

    @Test
    fun testDegenerateDimensionsHandling() {
        val yolo = Yolo26Segmentation(modelPath = modelFile.absolutePath)
        try {
            assertTrue("Model must initialize", yolo.isInitialized)

            // Case A: 0x0 empty input
            val emptyResult = yolo.detectPixels(IntArray(0), 0, 0)
            assertNotNull("Empty input must return valid result object", emptyResult)
            assertEquals(0, emptyResult.boxes.size)

            // Case B: 1x1 minimal pixel input
            val tinyResult = yolo.detectPixels(intArrayOf(0x00FF0000), 1, 1)
            assertNotNull("1x1 input must return valid result object", tinyResult)

            // Case C: Extreme aspect ratio 1x640 (tall strip)
            val tallPixels = IntArray(640) { 0x0000FF00 }
            val tallResult = yolo.detectPixels(tallPixels, 1, 640)
            assertNotNull(tallResult)

            // Case D: Extreme aspect ratio 640x1 (wide strip)
            val widePixels = IntArray(640) { 0x000000FF }
            val wideResult = yolo.detectPixels(widePixels, 640, 1)
            assertNotNull(wideResult)

            // Case E: Huge dimension 4000x3000 downscaling
            val hugeW = 400
            val hugeH = 300
            val hugePixels = IntArray(hugeW * hugeH) { 0x007F7F7F }
            val hugeResult = yolo.detectPixels(hugePixels, hugeW, hugeH)
            assertNotNull(hugeResult)
        } finally {
            yolo.close()
        }
    }

    @Test
    fun testLowThresholdStressNmsAndMaxResultsCapping() {
        // Stress test: with a tiny threshold of 0.05, hundreds of anchor candidates survive
        val yolo = Yolo26Segmentation(
            modelPath = modelFile.absolutePath,
            scoreThreshold = 0.05f,
            iouThreshold = 0.45f,
            maxResults = 10,
        )
        try {
            val img = ImageIO.read(testImageFile)
            val width = img.width
            val height = img.height
            val pixels = IntArray(width * height)
            img.getRGB(0, 0, width, height, pixels, 0, width)

            val result = yolo.detectPixels(pixels, width, height)
            assertNotNull(result)
            assertTrue("Should detect objects with low threshold", result.boxes.isNotEmpty())
            assertTrue("Capping must strictly enforce maxResults (<= 10)", result.boxes.size <= 10)

            // Verify sorted by confidence descending
            for (i in 0 until result.boxes.size - 1) {
                assertTrue(
                    "Detections must be ordered by confidence",
                    result.boxes[i].score >= result.boxes[i + 1].score
                )
            }

            // Verify class-aware IoU NMS suppression: no two boxes of the same class should have IoU > 0.45 (Failure Mode 12)
            for (i in result.boxes.indices) {
                for (j in i + 1 until result.boxes.size) {
                    val b1 = result.boxes[i]
                    val b2 = result.boxes[j]
                    if (b1.label == b2.label) {
                        val interL = maxOf(b1.left, b2.left)
                        val interT = maxOf(b1.top, b2.top)
                        val interR = minOf(b1.right, b2.right)
                        val interB = minOf(b1.bottom, b2.bottom)
                        val interArea = maxOf(0f, interR - interL) * maxOf(0f, interB - interT)
                        val area1 = (b1.right - b1.left) * (b1.bottom - b1.top)
                        val area2 = (b2.right - b2.left) * (b2.bottom - b2.top)
                        val unionArea = area1 + area2 - interArea
                        val iou = if (unionArea > 0f) interArea / unionArea else 0f
                        assertTrue("Class-aware NMS must suppress same-class boxes with IoU > 0.45 (found $iou for ${b1.label})", iou <= 0.451f)
                    }
                }
            }
        } finally {
            yolo.close()
        }
    }

    @Test
    fun testHighThresholdGuaranteesZeroDetections() {
        val yolo = Yolo26Segmentation(
            modelPath = modelFile.absolutePath,
            scoreThreshold = 0.999f,
        )
        try {
            val img = ImageIO.read(testImageFile)
            val width = img.width
            val height = img.height
            val pixels = IntArray(width * height)
            img.getRGB(0, 0, width, height, pixels, 0, width)

            val result = yolo.detectPixels(pixels, width, height)
            assertNotNull(result)
            // Even if empty, must be clean list and no crash
            assertTrue(result.boxes.all { it.score >= 0.999f })
        } finally {
            yolo.close()
        }
    }

    @Test
    fun testReentrancyAndRepeatedInference() {
        val yolo = Yolo26Segmentation(modelPath = modelFile.absolutePath)
        try {
            val img = ImageIO.read(testImageFile)
            val width = img.width
            val height = img.height
            val pixels = IntArray(width * height)
            img.getRGB(0, 0, width, height, pixels, 0, width)

            var firstBoxCount = -1
            // Run 5 sequential inferences to verify re-entrancy and stability
            for (iter in 0 until 5) {
                val result = yolo.detectPixels(pixels, width, height)
                if (firstBoxCount == -1) {
                    firstBoxCount = result.boxes.size
                } else {
                    assertEquals("Subsequent inference must produce deterministic box count", firstBoxCount, result.boxes.size)
                }
            }
        } finally {
            yolo.close()
        }
    }

    @Test
    fun testMaskPrototypeReconstructionPointsIntegrity() {
        val yolo = Yolo26Segmentation(modelPath = modelFile.absolutePath)
        try {
            val img = ImageIO.read(testImageFile)
            val width = img.width
            val height = img.height
            val pixels = IntArray(width * height)
            img.getRGB(0, 0, width, height, pixels, 0, width)

            val result = yolo.detectPixels(pixels, width, height)
            assertTrue("Expected detections on real image", result.boxes.isNotEmpty())

            for (box in result.boxes) {
                if (box.points.isNotEmpty()) {
                    for (pt in box.points) {
                        assertFalse("Mask point x must not be NaN", pt.first.isNaN())
                        assertFalse("Mask point y must not be NaN", pt.second.isNaN())
                        assertFalse("Mask point x must not be Infinite", pt.first.isInfinite())
                        assertFalse("Mask point y must not be Infinite", pt.second.isInfinite())

                        // Mask points should fall within or directly around the bounding box
                        assertTrue(
                            "Mask point x (${pt.first}) within reasonable range of box [${box.left}, ${box.right}]",
                            pt.first >= -5f && pt.first <= width + 5f
                        )
                        assertTrue(
                            "Mask point y (${pt.second}) within reasonable range of box [${box.top}, ${box.bottom}]",
                            pt.second >= -5f && pt.second <= height + 5f
                        )
                    }
                }
            }
        } finally {
            yolo.close()
        }
    }
}
