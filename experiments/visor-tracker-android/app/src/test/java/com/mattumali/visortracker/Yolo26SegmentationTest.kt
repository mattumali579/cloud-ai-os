package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import javax.imageio.ImageIO

class Yolo26SegmentationTest {

    private val modelFile = ModelBenchmarkTest.resolveModelFile()
    private val testImageFile = ModelBenchmarkTest.resolveTestImageFile()

    @Test
    fun testYolo26SegmentationModelLoads() {
        val yolo = Yolo26Segmentation(modelPath = modelFile.absolutePath)
        try {
            assertTrue("YOLO26n-seg should initialize session", yolo.isInitialized)
        } finally {
            yolo.close()
        }
    }

    @Test
    fun testRealImageInferenceBoundingBoxesAndMasks() {
        assertTrue("Test image must exist", testImageFile.exists())
        val img = ImageIO.read(testImageFile)
        assertNotNull("ImageIO must decode JPEG image", img)

        val width = img.width
        val height = img.height
        val pixels = IntArray(width * height)
        img.getRGB(0, 0, width, height, pixels, 0, width)

        val yolo = Yolo26Segmentation(
            modelPath = modelFile.absolutePath,
            scoreThreshold = 0.25f,
            iouThreshold = 0.45f,
            maxResults = 8,
        )

        try {
            assertTrue("Model must be initialized", yolo.isInitialized)
            val result = yolo.detectPixels(pixels, width, height)

            println("YOLO26n-seg detected ${result.boxes.size} objects in ${result.inferenceMs} ms")
            for ((index, box) in result.boxes.withIndex()) {
                println("  Box $index: label=${box.label}, score=${box.score}, bounds=[${box.left.toInt()}, ${box.top.toInt()}, ${box.right.toInt()}, ${box.bottom.toInt()}], maskPoints=${box.points.size}")
            }

            assertTrue("Should detect real objects in test_10942_cam.jpg", result.boxes.isNotEmpty())
            assertTrue("Turnaround latency should be recorded", result.inferenceMs > 0)

            val labels = result.boxes.map { it.label }
            // test_10942_cam.jpg has a laptop, person, bed, mouse etc.
            assertTrue(
                "Detections should include recognized COCO classes such as laptop or person",
                labels.contains("laptop") || labels.contains("person") || labels.contains("mouse") || labels.contains("bed")
            )

            for (box in result.boxes) {
                assertEquals("object", box.kind)
                assertTrue("Score >= 0.25", box.score >= 0.25f)
                assertTrue("Valid coordinates: left >= 0", box.left >= 0f)
                assertTrue("Valid coordinates: top >= 0", box.top >= 0f)
                assertTrue("Valid coordinates: right <= width", box.right <= width.toFloat() + 1f)
                assertTrue("Valid coordinates: bottom <= height", box.bottom <= height.toFloat() + 1f)
                assertTrue("Non-zero area", box.right > box.left && box.bottom > box.top)

                // Verify mask contour points
                if (box.points.isNotEmpty()) {
                    for (pt in box.points) {
                        assertTrue("Mask point x within frame bounds", pt.first in 0f..(width.toFloat() + 2f))
                        assertTrue("Mask point y within frame bounds", pt.second in 0f..(height.toFloat() + 2f))
                    }
                }
            }
        } finally {
            yolo.close()
        }
    }

    @Test
    fun testTrackIdsAssociationWithYolo26Detections() {
        val yolo = Yolo26Segmentation(modelPath = modelFile.absolutePath, scoreThreshold = 0.25f)
        val img = ImageIO.read(testImageFile)
        val width = img.width
        val height = img.height
        val pixels = IntArray(width * height)
        img.getRGB(0, 0, width, height, pixels, 0, width)

        try {
            val result = yolo.detectPixels(pixels, width, height)
            assertTrue("Expected detections", result.boxes.isNotEmpty())

            val tracker = TrackIds()
            val frame1Tracked = tracker.update(result.boxes)

            assertEquals(result.boxes.size, frame1Tracked.size)
            for (tracked in frame1Tracked) {
                assertTrue("Each detection assigned positive trackId", tracked.trackId > 0)
                assertFalse("Newly detected box is not held", tracked.held)
            }

            // Second frame with slightly jittered boxes (simulating motion across video frames)
            val jittered = result.boxes.map {
                it.copy(
                    left = it.left + 2f,
                    right = it.right + 2f,
                    top = it.top + 1f,
                    bottom = it.bottom + 1f,
                )
            }
            val frame2Tracked = tracker.update(jittered)
            assertEquals(frame1Tracked.size, frame2Tracked.size)

            val originalIds = frame1Tracked.map { it.trackId }.toSet()
            val secondIds = frame2Tracked.map { it.trackId }.toSet()
            assertEquals("Track IDs should persist across sequential frames with overlap", originalIds, secondIds)
        } finally {
            yolo.close()
        }
    }

    @Test
    fun testDetectionPresetConfiguration() {
        val preset = DetectionPreset.YOLO26_SEG
        assertEquals("yolo26n-seg.onnx", preset.modelAsset)
        assertEquals(640, preset.maxFrameWidth)
        assertEquals(0.25f, preset.scoreThreshold, 0.001f)
        assertEquals(8, preset.maxResults)
        assertTrue(preset.isYolo)
        assertEquals(DetectionPreset.YOLO26_SEG, DetectionPreset.read("YOLO26_SEG"))
    }

    @Test
    fun portraitCameraUsesCenteredLetterboxAndRoundTripsCoordinates() {
        val transform = Yolo26Segmentation.LetterboxTransform.forFrame(1080, 1440, 640)
        assertEquals(640, transform.scaledHeight)
        assertEquals(480, transform.scaledWidth)
        assertEquals(80, transform.padX)
        assertEquals(0, transform.padY)
        assertEquals(0f, transform.toFrameX(80f, 1080), 0.01f)
        assertEquals(1080f, transform.toFrameX(560f, 1080), 0.01f)
        assertEquals(720f, transform.toFrameY(320f, 1440), 0.01f)
    }

    @Test
    fun testYoloeOpenVocabSegmentationModelLoadsAndInfers() {
        val yoloeModelFile = File("C:/Users/Admin/cloud-ai-os-visor-tracker/yoloe-26n-seg.onnx")
        if (!yoloeModelFile.exists()) return
        val yolo = Yolo26Segmentation(modelPath = yoloeModelFile.absolutePath, scoreThreshold = 0.20f)
        try {
            assertTrue("YOLOE model should initialize", yolo.isInitialized)
            if (testImageFile.exists()) {
                val img = ImageIO.read(testImageFile)
                val width = img.width
                val height = img.height
                val pixels = IntArray(width * height)
                img.getRGB(0, 0, width, height, pixels, 0, width)
                val result = yolo.detectPixels(pixels, width, height)
                assertTrue("Inference time recorded", result.inferenceMs > 0)
                println("YOLOE-26n-seg detected ${result.boxes.size} objects in ${result.inferenceMs} ms")
                for (b in result.boxes) {
                    assertTrue("Label in YOLOE classes", Yolo26Segmentation.YOLOE_CLASSES.contains(b.label))
                    assertTrue("Valid coordinates", b.right > b.left && b.bottom > b.top)
                }
            }
        } finally {
            yolo.close()
        }
    }
}
