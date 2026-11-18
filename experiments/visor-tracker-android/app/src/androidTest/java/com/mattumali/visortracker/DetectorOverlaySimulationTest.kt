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
        val projected = CoverGeometry.project(result.width, result.height, width, height, objects.first())
        val onBox = greenPixelsOnBox(painted, projected)
        val fullBox = full + "greenPixelsOnProjectedBox=$onBox\\n"
        File(context.filesDir, "detector-stage.txt").writeText(fullBox)
        assertTrue("onDraw did not paint a box\\n$fullBox", green > 30 && onBox > 10)
    }

    /**
     * Real EfficientDet calls for one minute. Frames are shifted copies of
     * people.jpg, plus a recycled bitmap that must not stop the next call.
     */
    @Test(timeout = 180_000)
    fun repeatedInferenceKeepsRunningForSixtySeconds() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val base = instrumentation.context.assets.open("people.jpg").use { BitmapFactory.decodeStream(it) }
        assertTrue(base != null && base.width > 32)
        val pipeline = VisionPipeline(context, false, DetectionPreset.FAST)
        try {
            val warmup = pipeline.detect(base)
            val warmupObjects = warmup.boxes.filter { it.kind == "object" }
            assertTrue("warmup inference returned no object", warmupObjects.isNotEmpty())
            val flipped = flip(base)
            val flippedResult = pipeline.detect(flipped)
            flipped.recycle()
            val flippedBox = flippedResult.boxes.firstOrNull { it.kind == "object" }
            assertTrue("flipped frame returned no object", flippedBox != null)
            val originalCenter = (warmupObjects.first().left + warmupObjects.first().right) / 2f
            val flippedCenter = (flippedBox!!.left + flippedBox.right) / 2f
            val flipDelta = kotlin.math.abs(originalCenter - flippedCenter)

            val started = android.os.SystemClock.elapsedRealtime()
            var inferences = 0
            var objectFrames = 0
            var invalidFrames = 0
            var recovered = 0
            var paintedFrames = 0
            var movedBoxes = 0
            var lastLeft = warmupObjects.first().left
            var lastCentroid = Float.NaN
            var centroidMoves = 0
            while (android.os.SystemClock.elapsedRealtime() - started < 60_000) {
                if (inferences % 5 == 4) {
                    val junk = Bitmap.createBitmap(8, 8, Bitmap.Config.ARGB_8888)
                    junk.recycle()
                    try {
                        pipeline.detect(junk)
                    } catch (_: Exception) {
                        invalidFrames++
                    }
                }
                val dx = (inferences % 6) * 20
                val shifted = shift(base, dx)
                val result = try {
                    pipeline.detect(shifted)
                } finally {
                    shifted.recycle()
                }
                inferences++
                if (inferences % 5 == 0) {
                    File(context.filesDir, "replay-stage.txt").writeText(
                        "partial elapsedMs=${android.os.SystemClock.elapsedRealtime() - started} inferences=$inferences objectFrames=$objectFrames movedBoxes=$movedBoxes invalidFrames=$invalidFrames recovered=$recovered paintedFrames=$paintedFrames centroidMoves=$centroidMoves\n",
                    )
                }
                val box = result.boxes.firstOrNull { it.kind == "object" && it.trackId > 0 }
                if (box != null) {
                    objectFrames++
                    if (kotlin.math.abs(box.left - lastLeft) > 1f) movedBoxes++
                    lastLeft = box.left
                    val painted = paint(context, result)
                    val projected = CoverGeometry.project(result.width, result.height, painted.width, painted.height, box)
                    val onBox = greenPixelsOnBox(painted, projected)
                    if (onBox > 10) {
                        paintedFrames++
                        val centroid = greenCentroid(painted, projected)
                        if (!lastCentroid.isNaN() && kotlin.math.abs(centroid - lastCentroid) > 2f) centroidMoves++
                        lastCentroid = centroid
                    }
                    painted.recycle()
                    if (invalidFrames > recovered) recovered = invalidFrames
                }
            }
            val elapsed = android.os.SystemClock.elapsedRealtime() - started
            val report = buildString {
                appendLine("warmup objects=${warmupObjects.size}")
                appendLine("flip centers $originalCenter -> $flippedCenter delta=$flipDelta")
                appendLine("elapsedMs=$elapsed")
                appendLine("inferences=$inferences")
                appendLine("objectFrames=$objectFrames")
                appendLine("movedBoxes=$movedBoxes")
                appendLine("invalidFrames=$invalidFrames")
                appendLine("recovered=$recovered")
                appendLine("paintedFrames=$paintedFrames")
                appendLine("centroidMoves=$centroidMoves")
            }
            File(context.filesDir, "replay-stage.txt").writeText(report)
            assertTrue("replay shorter than 60s\n$report", elapsed >= 60_000)
            assertTrue("fewer than two real inferences\n$report", inferences >= 2)
            assertTrue("detector stopped returning objects\n$report", objectFrames >= 2)
            assertTrue("boxes did not change across shifted frames\n$report", movedBoxes >= 1)
            assertTrue("no invalid frame was injected\n$report", invalidFrames >= 1)
            assertTrue("pipeline did not recover after an invalid frame\n$report", recovered >= 1)
            assertTrue("onDraw did not paint the projected box\n$report", paintedFrames >= 1)
            assertTrue("painted box did not move\n$report", centroidMoves >= 1)
        } finally {
            pipeline.close()
        }
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
            val lockedId = view.selectedTrackId()
            if (lockedId != null) {
                assertEquals("handoff must publish the target selected by the lock",
                    lockedId, event.frame.boxes.single().trackId)
            }
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

    private fun shift(source: Bitmap, dx: Int): Bitmap {
        val out = Bitmap.createBitmap(source.width, source.height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out)
        canvas.drawColor(android.graphics.Color.DKGRAY)
        canvas.drawBitmap(source, dx.toFloat(), 0f, null)
        return out
    }

    private fun flip(source: Bitmap): Bitmap {
        val matrix = android.graphics.Matrix().apply {
            preScale(-1f, 1f, source.width / 2f, source.height / 2f)
        }
        return Bitmap.createBitmap(source, 0, 0, source.width, source.height, matrix, true)
    }

    private fun paint(context: android.content.Context, frame: FrameDetections): Bitmap {
        val view = TrackingOverlayView(context)
        val width = 480
        val height = 854
        view.measure(
            android.view.View.MeasureSpec.makeMeasureSpec(width, android.view.View.MeasureSpec.EXACTLY),
            android.view.View.MeasureSpec.makeMeasureSpec(height, android.view.View.MeasureSpec.EXACTLY),
        )
        view.layout(0, 0, width, height)
        view.setProbe(emptyList())
        view.updateFrame(frame.copy(sequence = frame.sequence.coerceAtLeast(1), capturedAtMs = frame.capturedAtMs.coerceAtLeast(1)))
        val painted = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        view.draw(Canvas(painted))
        return painted
    }

    private fun greenPixelsOnBox(bitmap: Bitmap, box: ProjectedBox): Int {
        var count = 0
        val left = (box.left - 8f).toInt().coerceIn(0, bitmap.width - 1)
        val right = (box.right + 8f).toInt().coerceIn(left, bitmap.width - 1)
        val top = (box.top - 8f).toInt().coerceIn(0, bitmap.height - 1)
        val bottom = (box.bottom + 8f).toInt().coerceIn(top, bitmap.height - 1)
        val width = right - left + 1
        val height = bottom - top + 1
        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, left, top, width, height)
        for (pixel in pixels) if (isBoxGreen(pixel)) count++
        return count
    }

    private fun greenCentroid(bitmap: Bitmap, box: ProjectedBox): Float {
        var count = 0
        var sum = 0f
        val left = (box.left - 8f).toInt().coerceIn(0, bitmap.width - 1)
        val right = (box.right + 8f).toInt().coerceIn(left, bitmap.width - 1)
        val top = (box.top - 8f).toInt().coerceIn(0, bitmap.height - 1)
        val bottom = (box.bottom + 8f).toInt().coerceIn(top, bitmap.height - 1)
        val width = right - left + 1
        val height = bottom - top + 1
        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, left, top, width, height)
        for (index in pixels.indices) {
            if (!isBoxGreen(pixels[index])) continue
            count++
            sum += left + (index % width)
        }
        return if (count == 0) Float.NaN else sum / count
    }

    private fun isBoxGreen(pixel: Int): Boolean {
        val red = (pixel shr 16) and 0xff
        val green = (pixel shr 8) and 0xff
        val blue = pixel and 0xff
        return green > 140 && green > red + 30 && green > blue
    }

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
