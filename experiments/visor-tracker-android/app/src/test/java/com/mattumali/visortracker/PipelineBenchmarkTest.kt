package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * CPU timings for the two pure stages that do not need a phone or MediaPipe.
 * Printed medians are measurements of this JVM, not Galaxy S22 Ultra latency.
 */
class PipelineBenchmarkTest {
    @Test fun yuvDownscaleAndMotionAdvanceStayBounded() {
        val width = 1280
        val height = 720
        val y = ByteArray(width * height) { 140.toByte() }
        val uvWidth = width / 2
        val uv = ByteArray(uvWidth * (height / 2)) { 128.toByte() }
        val rgb = IntArray(384 * 216)
        val yuvMs = medianMicros(5) {
            val (outW, outH) = YuvSnapshot.writeArgb(
                width, height, 384,
                y, y.size, width, 1, 0, 0,
                uv, uv.size, uvWidth, 1, 0, 0,
                uv, uv.size, uvWidth, 1, 0, 0,
                rgb,
            )
            assertEquals(384, outW)
            assertEquals(216, outH)
        }
        assertTrue(384 * 216 < width * height / 4)
        val motionMs = medianMicros(20) {
            val tracker = MotionTracker()
            tracker.reset(FrameDetections(192, 108, listOf(
                BoxObservation("object", "chair", 0.9f, 40f, 30f, 90f, 80f, trackId = 1),
                BoxObservation("object", "cup", 0.8f, 100f, 40f, 130f, 80f, trackId = 2),
                BoxObservation("object", "book", 0.8f, 20f, 60f, 70f, 100f, trackId = 3),
            )), textured(192, 108, 0))
            val moved = tracker.advance(textured(192, 108, 4))
            assertNotNull(moved)
            assertEquals(3, moved!!.boxes.size)
        }
        println("pipeline bench median us: yuv1280x720to384=$yuvMs motion3boxes=$motionMs")
        assertTrue("yuv median ${yuvMs}us", yuvMs in 1..80_000)
        assertTrue("motion median ${motionMs}us", motionMs in 1..80_000)
    }

    private fun textured(width: Int, height: Int, dx: Int): GrayFrame {
        val pixels = ByteArray(width * height) { 20 }
        for (y in 20 until height - 20) {
            for (x in 20 until width - 20) {
                val src = x - dx
                if (src in 0 until width) {
                    pixels[y * width + x] = ((src * 17 + y * 13) % 190 + 40).toByte()
                }
            }
        }
        return GrayFrame(width, height, pixels)
    }

    private fun medianMicros(times: Int, block: () -> Unit): Long {
        block()
        val samples = LongArray(times) {
            val started = System.nanoTime()
            block()
            (System.nanoTime() - started) / 1_000
        }
        samples.sort()
        return samples[samples.size / 2]
    }
}
