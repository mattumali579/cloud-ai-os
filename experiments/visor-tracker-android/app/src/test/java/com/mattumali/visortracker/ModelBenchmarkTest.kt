package com.mattumali.visortracker

import ai.onnxruntime.OrtEnvironment
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import javax.imageio.ImageIO

class ModelBenchmarkTest {

    companion object {
        fun resolveModelFile(): File {
            val candidates = listOf(
                File("experiments/visor-tracker-android/app/src/main/assets/yolo26n-seg.onnx"),
                File("experiments/visor-tracker-android/app/src/main/assets/models/yolo26n-seg.onnx"),
                File("app/src/main/assets/yolo26n-seg.onnx"),
                File("app/src/main/assets/models/yolo26n-seg.onnx"),
                File("src/main/assets/yolo26n-seg.onnx"),
                File("src/main/assets/models/yolo26n-seg.onnx"),
                File("yolo26n-seg.onnx"),
                File("../../../yolo26n-seg.onnx"),
                File("C:/Users/Admin/cloud-ai-os-visor-tracker/yolo26n-seg.onnx"),
                File("C:/Users/Admin/cloud-ai-os-visor-tracker/experiments/visor-tracker-android/app/src/main/assets/yolo26n-seg.onnx"),
            )
            return candidates.map { it.absoluteFile }.firstOrNull { it.isFile && it.length() > 0 } ?: File("yolo26n-seg.onnx").absoluteFile
        }

        fun resolveTestImageFile(): File {
            val candidates = listOf(
                File("experiments/visor-tracker-android/app/src/test/resources/test_10942_cam.jpg"),
                File("app/src/test/resources/test_10942_cam.jpg"),
                File("src/test/resources/test_10942_cam.jpg"),
                File("experiments/visor-tracker-android/app/src/androidTest/assets/people.jpg"),
                File("app/src/androidTest/assets/people.jpg"),
                File("src/androidTest/assets/people.jpg"),
                File("test-recordings/test_10942_cam.jpg"),
                File("../../../test-recordings/test_10942_cam.jpg"),
                File("C:/Users/Admin/cloud-ai-os-visor-tracker/test-recordings/test_10942_cam.jpg"),
                File("C:/Users/Admin/cloud-ai-os-visor-tracker/experiments/visor-tracker-android/app/src/test/resources/test_10942_cam.jpg"),
            )
            return candidates.map { it.absoluteFile }.firstOrNull { it.isFile && it.length() > 0 } ?: File("test_10942_cam.jpg").absoluteFile
        }
    }

    @Test
    fun benchmarkYolo26nSeg() {
        val modelFile = resolveModelFile()
        val testImageFile = resolveTestImageFile()
        assumeTrue("YOLO26n-seg model file required", modelFile.exists() && modelFile.length() > 0)
        assumeTrue("Test image required", testImageFile.exists() && testImageFile.length() > 0)

        println("=== Benchmarking YOLO26n-seg (ONNX Runtime) ===")
        println("Model file size: ${modelFile.length() / (1024 * 1024.0)} MB")

        val img = ImageIO.read(testImageFile)
        val width = img.width
        val height = img.height
        val pixels = IntArray(width * height)
        img.getRGB(0, 0, width, height, pixels, 0, width)

        val yolo = Yolo26Segmentation(modelPath = modelFile.absolutePath, scoreThreshold = 0.25f)
        try {
            // Warmup
            for (i in 0 until 3) {
                yolo.detectPixels(pixels, width, height)
            }

            // Benchmark 10 iterations
            val iterations = 10
            val latencies = LongArray(iterations)
            val runtime = Runtime.getRuntime()
            runtime.gc()
            val memBefore = runtime.totalMemory() - runtime.freeMemory()

            for (i in 0 until iterations) {
                val start = System.nanoTime()
                val res = yolo.detectPixels(pixels, width, height)
                latencies[i] = (System.nanoTime() - start) / 1_000_000 // ms
            }

            val memAfter = runtime.totalMemory() - runtime.freeMemory()
            latencies.sort()
            val medianMs = latencies[iterations / 2]
            val minMs = latencies[0]
            val maxMs = latencies[iterations - 1]
            val fps = 1000.0 / medianMs

            println("YOLO26n-seg Latency: min=${minMs}ms, median=${medianMs}ms, max=${maxMs}ms")
            println("YOLO26n-seg FPS: ${"%.1f".format(fps)}")
            println("Estimated memory delta: ${(memAfter - memBefore) / (1024 * 1024.0)} MB")
        } finally {
            yolo.close()
        }
    }
}
