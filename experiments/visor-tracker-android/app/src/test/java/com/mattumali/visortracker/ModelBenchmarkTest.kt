package com.mattumali.visortracker

import ai.onnxruntime.OrtEnvironment
import org.junit.Test
import java.io.File
import javax.imageio.ImageIO

class ModelBenchmarkTest {

    @Test
    fun benchmarkYolo26nSeg() {
        val modelFile = File("C:/Users/Admin/cloud-ai-os-visor-tracker/yolo26n-seg.onnx")
        val testImageFile = File("C:/Users/Admin/cloud-ai-os-visor-tracker/test-recordings/test_10942_cam.jpg")

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
