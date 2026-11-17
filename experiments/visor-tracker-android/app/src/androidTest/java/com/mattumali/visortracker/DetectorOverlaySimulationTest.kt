package com.mattumali.visortracker

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Runs real EfficientDet-Lite0 and paints the real overlay.
 * This is not a Visor, glasses, or Samsung stacking test.
 */
@RunWith(AndroidJUnit4::class)
class DetectorOverlaySimulationTest {
    @Test(timeout = 120_000)
    fun realDetectorReturnsABoxAndTheOverlayPaintsIt() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val started = android.os.SystemClock.elapsedRealtime()
        val input = instrumentation.context.assets.open("people.jpg").use { BitmapFactory.decodeStream(it) }
        assertTrue("people.jpg did not decode", input != null && input.width > 0)
        val pipeline = VisionPipeline(context, false, DetectionPreset.FAST)
        val result = try {
            pipeline.detect(input)
        } finally {
            pipeline.close()
        }
        val elapsed = android.os.SystemClock.elapsedRealtime() - started
        val objects = result.boxes.filter { it.kind == "object" && it.trackId > 0 }
        val stage = buildString {
            appendLine("decoder/replay -> RGB ready ${input.width}x${input.height}")
            appendLine("inference start/completed in ${elapsed}ms")
            appendLine("model returns ${objects.size} object(s) on ${result.width}x${result.height}")
            appendLine("selected ID ${objects.firstOrNull()?.trackId}")
        }
        assertTrue("detector did not finish within 60s\n$stage", elapsed < 60_000)
        assertTrue("EfficientDet-Lite0 returned no object on people.jpg\n$stage", objects.isNotEmpty())
        assertTrue(result.width > 0 && result.height > 0)

        val view = TrackingOverlayView(context)
        val width = 480
        val height = 854
        view.measure(
            android.view.View.MeasureSpec.makeMeasureSpec(width, android.view.View.MeasureSpec.EXACTLY),
            android.view.View.MeasureSpec.makeMeasureSpec(height, android.view.View.MeasureSpec.EXACTLY),
        )
        view.layout(0, 0, width, height)
        view.updateFrame(result.copy(sequence = 1, capturedAtMs = 1))
        val painted = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        view.draw(Canvas(painted))
        val green = countGreen(painted)
        val full = stage + "published 1\nonDraw greenPixels=$green\n"
        File(context.filesDir, "detector-stage.txt").writeText(full)
        File(context.filesDir, "overlay-sim.png").outputStream().use {
            painted.compress(Bitmap.CompressFormat.PNG, 100, it)
        }
        assertTrue("onDraw did not paint a box\n$full", green > 30)
    }

    @Test fun oneBoxSwitchesFromAtoBOnlyAfterTwoDetectorFrames() {
        val aim = AimLock()
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val view = TrackingOverlayView(context)
        view.measure(
            android.view.View.MeasureSpec.makeMeasureSpec(200, android.view.View.MeasureSpec.EXACTLY),
            android.view.View.MeasureSpec.makeMeasureSpec(200, android.view.View.MeasureSpec.EXACTLY),
        )
        view.layout(0, 0, 200, 200)
        val first = frame(box(1, 40f), box(2, 0f))
        assertFalse(aim.observe(first).changed)
        val locked = aim.observe(first)
        assertEquals(1, locked.lockedId)
        view.updateFrame(first.copy(boxes = listOf(first.boxes.first { it.trackId == 1 }), sequence = 2))
        assertEquals(1, view.drawnBoxCount())
        val towardB = frame(box(1, 0f), box(2, 40f))
        assertEquals(1, aim.observe(towardB).lockedId)
        val switched = aim.observe(towardB)
        assertEquals(2, switched.lockedId)
        val onlyB = towardB.copy(boxes = listOf(towardB.boxes.first { it.trackId == 2 }), sequence = 4)
        view.updateFrame(onlyB)
        assertEquals(1, view.drawnBoxCount())
        val back = frame(box(1, 40f), box(2, 0f))
        aim.observe(back)
        assertEquals(1, aim.observe(back).lockedId)
    }

    private fun box(id: Int, x: Float) =
        BoxObservation("object", "chair", 0.8f, x, 40f, x + 20f, 80f, trackId = id)

    private fun frame(vararg boxes: BoxObservation) =
        FrameDetections(100, 100, boxes.toList(), sequence = 1, capturedAtMs = 1)

    private fun countGreen(bitmap: Bitmap): Int {
        var count = 0
        val pixels = IntArray(bitmap.width * bitmap.height)
        bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
        for (pixel in pixels) {
            val red = (pixel shr 16) and 0xff
            val green = (pixel shr 8) and 0xff
            val blue = pixel and 0xff
            if (green > 140 && green > red + 30 && green > blue) count += 1
        }
        return count
    }
}
