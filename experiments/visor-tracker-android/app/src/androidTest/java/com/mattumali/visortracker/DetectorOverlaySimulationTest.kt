package com.mattumali.visortracker

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import org.junit.Assert.assertEquals
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
        assertTrue("detector did not finish within 60s\\n$stage", elapsed < 60_000)
        assertTrue("EfficientDet-Lite0 returned no object on people.jpg\\n$stage", objects.isNotEmpty())
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
        val full = stage + "published 1\\nonDraw greenPixels=$green\\n"
        File(context.filesDir, "detector-stage.txt").writeText(full)
        File(context.filesDir, "overlay-sim.png").outputStream().use {
            painted.compress(Bitmap.CompressFormat.PNG, 100, it)
        }
        assertTrue("onDraw did not paint a box\\n$full", green > 30)
    }

    @Test
    fun productionSelectionSwitchesAtoBAndBackOnlyAfterTwoDetectorFrames() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val view = TrackingOverlayView(context)
        view.measure(
            android.view.View.MeasureSpec.makeMeasureSpec(200, android.view.View.MeasureSpec.EXACTLY),
            android.view.View.MeasureSpec.makeMeasureSpec(200, android.view.View.MeasureSpec.EXACTLY),
        )
        view.layout(0, 0, 200, 200)
        val handoff = TrackingHandoff()
        var sequence = 0L

        fun detectorFrame(aX: Float, bX: Float): FrameDetections {
            sequence += 1
            return FrameDetections(
                width = 100,
                height = 100,
                boxes = listOf(box(1, aX), box(2, bX)),
                sequence = sequence,
                capturedAtMs = sequence * 250,
            )
        }

        // Follow the production service order: let the overlay inspect every
        // detector candidate, offer the corresponding decoded frame, run the
        // detector-to-motion handoff with the overlay's current lock, then draw
        // the returned frame through TrackingOverlayView.updateFrame().
        fun process(frame: FrameDetections): Int {
            view.observeCandidates(frame)
            val gray = GrayFrame(
                width = frame.width,
                height = frame.height,
                bytes = ByteArray(frame.width * frame.height) { 127 },
                sequence = frame.sequence,
                capturedAtMs = frame.capturedAtMs,
            )
            handoff.offer(gray)
            val event = handoff.pump(
                nowMs = frame.capturedAtMs,
                detection = DetectionBaseline(frame, gray),
                lockedId = view.selectedTrackId(),
            )
            assertTrue("handoff did not publish detector frame #${frame.sequence}", event != null)
            view.updateFrame(event!!.frame)
            assertEquals("overlay must draw exactly one target", 1, view.drawnBoxCount())
            assertEquals("handoff must publish the target selected by the lock",
                view.selectedTrackId(), event.frame.boxes.single().trackId)
            return event.frame.boxes.single().trackId
        }

        // A is centered twice, so the normal lock path acquires A.
        assertEquals(1, process(detectorFrame(aX = 40f, bX = 80f)))
        assertEquals(1, process(detectorFrame(aX = 40f, bX = 80f)))
        assertEquals(1, view.selectedTrackId())

        // B becomes centered. The first detector frame must retain A.
        assertEquals(1, process(detectorFrame(aX = 0f, bX = 40f)))
        // The second distinct detector frame completes the switch to B.
        assertEquals(2, process(detectorFrame(aX = 0f, bX = 40f)))
        assertEquals(2, view.selectedTrackId())

        // Reversing the aim follows the same two-frame rule and returns to A.
        assertEquals(2, process(detectorFrame(aX = 40f, bX = 80f)))
        assertEquals(1, process(detectorFrame(aX = 40f, bX = 80f)))
        assertEquals(1, view.selectedTrackId())
    }

    private fun box(id: Int, x: Float) =
        BoxObservation("object", "chair", 0.8f, x, 40f, x + 20f, 80f, trackId = id)

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
