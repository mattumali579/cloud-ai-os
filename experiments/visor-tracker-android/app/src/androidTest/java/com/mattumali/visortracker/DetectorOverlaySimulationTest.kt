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

    /**
     * Real Hand Landmarker and EfficientDet on people.jpg, which shows an
     * extended index finger aimed across two people. The production ray and
     * AimLock select one person, keep that id while the picture shifts, then
     * take the remaining person after the first one is covered.
     */
    @Test(timeout = 300_000)
    fun realHandPointsAtOneObjectThenTheOtherIsAcquired() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val base = instrumentation.context.assets.open("people.jpg").use { BitmapFactory.decodeStream(it) }
        assertTrue(base != null && base.width > 32)
        val pipeline = VisionPipeline(context, includeFaces = false, preset = DetectionPreset.FAST, includeHands = true)
        val report = StringBuilder()
        fun save(extra: String = "") {
            File(context.filesDir, "pointing-stage.txt").writeText(report.toString() + extra)
        }
        try {
            val firstStarted = android.os.SystemClock.elapsedRealtime()
            val first = pipeline.detect(base)
            val firstMs = android.os.SystemClock.elapsedRealtime() - firstStarted
            val hands = first.boxes.filter { it.kind == "hand" && it.points.size >= 21 }
            val objects = first.boxes.filter { it.kind == "object" && it.trackId > 0 }
            val ray = IndexPoint.ray(first)
            val pointed = if (ray == null) null else IndexPoint.alongRay(ray, first.boxes, null)
            val center = objects.minByOrNull { box ->
                val cx = (box.left + box.right) / (2f * first.width) - 0.5f
                val cy = (box.top + box.bottom) / (2f * first.height) - 0.5f
                cx * cx + cy * cy
            }
            report.appendLine("frame ${first.width}x${first.height} photo ${base.width}x${base.height}")
            report.appendLine(
                "firstDetectMs=$firstMs bitmapMs=${pipeline.lastBitmapMs} objectMs=${pipeline.lastObjectMs} " +
                    "handMs=${pipeline.lastHandMs} wallMs=${pipeline.lastWallMs} overlap=${pipeline.overlapModels}",
            )
            report.appendLine("yuvMs=not-measured-this-replay-has-no-camera")
            report.appendLine(
                "handDelegate=${pipeline.handDelegate} attempted=${pipeline.handDelegateAttempt} " +
                    "fallbacks=${pipeline.handFallbacks} objectDelegate=${pipeline.objectDelegate}",
            )
            report.appendLine("delegateNote=phone battery and thermal throttling were not measured")
            report.appendLine("hands=${hands.size} objects=${objects.size} fault=${pipeline.lastOptionalFault}")
            objects.forEach { report.appendLine("object #${it.trackId} ${it.label} ${it.score} ${it.left},${it.top},${it.right},${it.bottom}") }
            hands.forEach { hand ->
                val wrist = hand.points[IndexPoint.WRIST]
                val mcp = hand.points[IndexPoint.INDEX_MCP]
                val pip = hand.points[IndexPoint.INDEX_PIP]
                val dip = hand.points[IndexPoint.INDEX_DIP]
                val tip = hand.points[IndexPoint.INDEX_TIP]
                report.appendLine("hand score=${hand.score} wrist=$wrist mcp=$mcp pip=$pip dip=$dip tip=$tip")
            }
            if (ray != null) report.appendLine("ray tip=${ray.tipX},${ray.tipY} dir=${ray.dirX},${ray.dirY}")
            report.appendLine("rayTarget=${pointed?.trackId} centerTarget=${center?.trackId}")
            report.appendLine(IndexPoint.LIMITATION)
            save()
            assertTrue("Hand Landmarker returned no 21-point hand\n$report", hands.isNotEmpty())
            assertTrue("index finger was not extended\n$report", ray != null && ray.joints.size == 5)
            assertTrue("EfficientDet did not return two objects\n$report", objects.size >= 2)
            assertTrue("finger ray did not meet an object\n$report", pointed != null)

            val aim = AimLock()
            val lockStarted = android.os.SystemClock.elapsedRealtime()
            aim.observe(first)
            val locked = aim.observe(first)
            val pointToLockMs = android.os.SystemClock.elapsedRealtime() - lockStarted
            report.appendLine("lockA=${locked.lockedId} fingerLocked=${aim.fingerLocked} pointToLockMs=$pointToLockMs")
            save()
            assertEquals("finger did not lock the object on the ray\n$report", pointed!!.trackId, locked.lockedId)
            assertTrue(aim.fingerLocked)

            val shifted = shift(base, 36)
            val moved = try {
                pipeline.detect(shifted)
            } finally {
                shifted.recycle()
            }
            val still = aim.observe(moved)
            val stillThere = moved.boxes.any { it.kind == "object" && it.trackId == pointed.trackId }
            report.appendLine("afterShift lock=${still.lockedId} targetStillVisible=$stillThere")
            save()
            assertTrue("shifted frame lost the pointed track\n$report", stillThere)
            assertEquals("lock changed while the pointed object was still visible\n$report", pointed.trackId, still.lockedId)

            val covered = cover(base, pointed, first.width, first.height)
            val switchStarted = android.os.SystemClock.elapsedRealtime()
            var replacement: Int? = null
            var switchMs = 0L
            var switchPasses = 0
            var knownCandidateFrames = 0
            var reusedKnownFrame = false
            var objectSkipped = false
            try {
                val fresh = FreshObjectReuse.replacementIsFresh(moved, pointed.trackId, ageMs = 0L)
                val beforePatch = RegionChange.sample(base, pointed, first.width, first.height)
                val afterPatch = RegionChange.sample(covered, pointed, first.width, first.height)
                val obscured = beforePatch != null && afterPatch != null &&
                    RegionChange.obscured(beforePatch, afterPatch)
                val changedSamples = if (beforePatch == null || afterPatch == null) {
                    -1
                } else {
                    val count = minOf(beforePatch.size, afterPatch.size)
                    (0 until count).count { RegionChange.channelDelta(beforePatch[it], afterPatch[it]) > RegionChange.CHANNEL_DELTA }
                }
                report.appendLine(
                    "replacementFresh=$fresh regionObscured=$obscured changedSamples=$changedSamples/${beforePatch?.size ?: 0}",
                )
                if (fresh && obscured) {
                    switchPasses = 1
                    val detectStarted = android.os.SystemClock.elapsedRealtime()
                    val hands = pipeline.detect(covered, runObjects = false, runHands = true)
                    val skipWall = android.os.SystemClock.elapsedRealtime() - detectStarted
                    val aligned = moved.boxes.mapNotNull { box ->
                        if (box.kind != "object" || box.held || box.trackId == pointed.trackId) return@mapNotNull null
                        first.boxes.firstOrNull { it.kind == "object" && it.trackId == box.trackId } ?: box
                    }
                    val frame = FrameDetections(
                        width = first.width,
                        height = first.height,
                        boxes = aligned + hands.boxes.filter { it.kind == "hand" },
                    )
                    if (aligned.isNotEmpty()) knownCandidateFrames++
                    val known = KnownReplacement.alreadyVisible(moved, frame, pointed.trackId)
                    reusedKnownFrame = known
                    val decision = if (known) aim.observeKnownReplacement(frame) else aim.observe(frame)
                    val visible = frame.boxes.filter { it.kind == "object" }.joinToString { "#${it.trackId}" }
                    report.appendLine(
                        "objectSkipped=true skipWallMs=$skipWall bitmapMs=${pipeline.lastBitmapMs} " +
                            "objectMs=0 handMs=${pipeline.lastHandMs} wallMs=${pipeline.lastWallMs}",
                    )
                    report.appendLine(
                        "occluded lock=${decision.lockedId} visible=$visible knownFrames=$knownCandidateFrames known=$known",
                    )
                    if (acceptedSwitch(decision, frame, pointed.trackId)) {
                        objectSkipped = true
                        replacement = decision.lockedId
                        switchMs = android.os.SystemClock.elapsedRealtime() - switchStarted
                        report.appendLine(
                            "switchFirstDetectMs=$skipWall objectMs=0 handMs=${pipeline.lastHandMs}",
                        )
                    } else {
                        report.appendLine("objectSkipRejected=true fallback=full-detect")
                    }
                }
                if (replacement == null) {
                    switchPasses = if (switchPasses == 0) 1 else switchPasses + 1
                    val detectStarted = android.os.SystemClock.elapsedRealtime()
                    val frame = pipeline.detect(covered)
                    val switchDetectMs = android.os.SystemClock.elapsedRealtime() - detectStarted
                    val oldVisible = frame.boxes.any { it.kind == "object" && it.trackId == pointed.trackId }
                    val otherVisible = frame.boxes.any { it.kind == "object" && it.trackId > 0 && it.trackId != pointed.trackId }
                    if (!oldVisible && otherVisible) knownCandidateFrames++
                    val known = KnownReplacement.alreadyVisible(moved, frame, pointed.trackId)
                    reusedKnownFrame = known
                    val decision = if (known) aim.observeKnownReplacement(frame) else aim.observe(frame)
                    val visible = frame.boxes.filter { it.kind == "object" }.joinToString { "#${it.trackId}" }
                    report.appendLine(
                        "occluded lock=${decision.lockedId} visible=$visible knownFrames=$knownCandidateFrames known=$known",
                    )
                    report.appendLine(
                        "switchFirstDetectMs=$switchDetectMs bitmapMs=${pipeline.lastBitmapMs} " +
                            "objectMs=${pipeline.lastObjectMs} handMs=${pipeline.lastHandMs} wallMs=${pipeline.lastWallMs}",
                    )
                    if (acceptedSwitch(decision, frame, pointed.trackId)) {
                        replacement = decision.lockedId
                        switchMs = android.os.SystemClock.elapsedRealtime() - switchStarted
                    } else {
                        repeat(7) {
                            if (replacement != null) return@repeat
                            switchPasses++
                            val again = pipeline.detect(covered)
                            val oldAgain = again.boxes.any { it.kind == "object" && it.trackId == pointed.trackId }
                            val otherAgain = again.boxes.any {
                                it.kind == "object" && it.trackId > 0 && it.trackId != pointed.trackId
                            }
                            if (!oldAgain && otherAgain) knownCandidateFrames++
                            val next = aim.observe(again)
                            val shown = again.boxes.filter { it.kind == "object" }.joinToString { "#${it.trackId}" }
                            report.appendLine("occluded lock=${next.lockedId} visible=$shown knownFrames=$knownCandidateFrames")
                            if (acceptedSwitch(next, again, pointed.trackId)) {
                                replacement = next.lockedId
                                switchMs = android.os.SystemClock.elapsedRealtime() - switchStarted
                                return@repeat
                            }
                        }
                    }
                }
                report.appendLine("objectSkipped=$objectSkipped")
            } finally {
                covered.recycle()
            }
            report.appendLine("lockB=$replacement switchToBMs=$switchMs")
            report.appendLine(
                "switchDetectPasses=$switchPasses knownCandidateFrames=$knownCandidateFrames reusedKnownFrame=$reusedKnownFrame",
            )
            report.appendLine(
                "switchNote=when the replacement was already visible, the second AimLock observation reuses that detection and does not run another hand+object detect",
            )
            save()
            assertTrue("covering the pointed object did not acquire the other one\n$report", replacement != null)

            val view = TrackingOverlayView(context)
            // Same 16:9 shape as the detection frame, so cover-crop does not
            // clip the fingertip that sits at the right edge.
            val viewWidth = 1280
            val viewHeight = 720
            view.measure(
                android.view.View.MeasureSpec.makeMeasureSpec(viewWidth, android.view.View.MeasureSpec.EXACTLY),
                android.view.View.MeasureSpec.makeMeasureSpec(viewHeight, android.view.View.MeasureSpec.EXACTLY),
            )
            view.layout(0, 0, viewWidth, viewHeight)
            view.observeCandidates(first.copy(sequence = 1, capturedAtMs = 1))
            view.observeCandidates(first.copy(sequence = 2, capturedAtMs = 2))
            view.updateFrame(first.copy(sequence = 2, capturedAtMs = 2))
            val painted = Bitmap.createBitmap(viewWidth, viewHeight, Bitmap.Config.ARGB_8888)
            view.draw(Canvas(painted))
            val projected = CoverGeometry.project(first.width, first.height, viewWidth, viewHeight, pointed)
            val white = whitePixelsOnBox(painted, projected)
            val cyan = countCyan(painted)
            report.appendLine("overlay whiteOnLock=$white cyanRay=$cyan drawn=${view.drawnBoxCount()} selected=${view.selectedTrackId()}")
            File(context.filesDir, "pointing-overlay.png").outputStream().use {
                painted.compress(Bitmap.CompressFormat.PNG, 100, it)
            }
            painted.recycle()
            save()
            assertTrue("locked box was not painted white\n$report", white > 10)
            assertTrue("pointing ray was not painted\n$report", cyan > 10)

            val started = android.os.SystemClock.elapsedRealtime()
            var invalidFrames = 0
            var recovered = 0
            var objectFrames = 0
            var steps = 0
            var detectMs = 0L
            var motionSteps = 0
            var motionMoved = 0
            var drawCount = 0
            val motion = MotionTracker()
            var motionReady = false
            val rateView = TrackingOverlayView(context)
            rateView.measure(
                android.view.View.MeasureSpec.makeMeasureSpec(160, android.view.View.MeasureSpec.EXACTLY),
                android.view.View.MeasureSpec.makeMeasureSpec(90, android.view.View.MeasureSpec.EXACTLY),
            )
            rateView.layout(0, 0, 160, 90)
            val rateBitmap = Bitmap.createBitmap(160, 90, Bitmap.Config.ARGB_8888)
            val rateCanvas = Canvas(rateBitmap)
            val beforeObjects = pipeline.objectInferences
            val beforeHands = pipeline.handInferences
            val stageMedians = StageTimings()
            val replayLock = AimLock()
            while (android.os.SystemClock.elapsedRealtime() - started < 60_000) {
                if (steps % 5 == 4) {
                    val junk = Bitmap.createBitmap(8, 8, Bitmap.Config.ARGB_8888)
                    junk.recycle()
                    try {
                        pipeline.detect(junk)
                    } catch (_: Exception) {
                        invalidFrames++
                    }
                }
                steps++
                val dx = (steps % 6) * 16
                val sample = shift(base, dx)
                try {
                    val detectStarted = android.os.SystemClock.elapsedRealtime()
                    val result = pipeline.detect(sample)
                    detectMs += android.os.SystemClock.elapsedRealtime() - detectStarted
                    stageMedians.record("b", pipeline.lastBitmapMs)
                    stageMedians.record("o", pipeline.lastObjectMs)
                    stageMedians.record("h", pipeline.lastHandMs)
                    stageMedians.record("w", pipeline.lastWallMs)
                    if (result.boxes.any { it.kind == "object" }) {
                        objectFrames++
                        if (invalidFrames > recovered) recovered = invalidFrames
                    }
                    val motionStarted = android.os.SystemClock.elapsedRealtime()
                    val movedByMotion = stepReplayMotion(motion, result, steps, motionReady)
                    stageMedians.record("m", android.os.SystemClock.elapsedRealtime() - motionStarted)
                    motionReady = true
                    motionSteps++
                    if (movedByMotion) motionMoved++
                    val lockStarted = android.os.SystemClock.elapsedRealtime()
                    replayLock.observe(result)
                    stageMedians.record("l", android.os.SystemClock.elapsedRealtime() - lockStarted)
                    rateView.updateFrame(
                        result.copy(sequence = steps.toLong(), capturedAtMs = steps.toLong()),
                    )
                    val drawStarted = android.os.SystemClock.elapsedRealtime()
                    rateView.draw(rateCanvas)
                    stageMedians.record("draw", android.os.SystemClock.elapsedRealtime() - drawStarted)
                    drawCount++
                } finally {
                    sample.recycle()
                }
            }
            rateBitmap.recycle()
            val elapsed = android.os.SystemClock.elapsedRealtime() - started
            val objectCalls = pipeline.objectInferences - beforeObjects
            val handCalls = pipeline.handInferences - beforeHands
            report.appendLine("elapsedMs=$elapsed")
            report.appendLine("objectInferences=$objectCalls handInferences=$handCalls")
            report.appendLine("cameraFps=not-measured")
            report.appendLine("cameraNote=this replay calls detect() on still images. It is not a camera and not a phone.")
            report.appendLine("objectFps=${"%.2f".format(java.util.Locale.US, objectCalls * 1000f / elapsed)}")
            report.appendLine("handFps=${"%.2f".format(java.util.Locale.US, handCalls * 1000f / elapsed)}")
            val detectSeconds = (detectMs.coerceAtLeast(1L)) / 1000f
            report.appendLine("objectFpsDetectOnly=${"%.2f".format(java.util.Locale.US, objectCalls / detectSeconds)}")
            report.appendLine("handFpsDetectOnly=${"%.2f".format(java.util.Locale.US, handCalls / detectSeconds)}")
            report.appendLine("motionSteps=$motionSteps motionMoved=$motionMoved")
            report.appendLine("motionFps=${"%.2f".format(java.util.Locale.US, motionSteps * 1000f / elapsed)}")
            report.appendLine("drawCount=$drawCount")
            report.appendLine("drawFps=${"%.2f".format(java.util.Locale.US, drawCount * 1000f / elapsed)}")
            report.appendLine("redrawIsNotInference=true")
            report.appendLine("detectMs=$detectMs")
            report.appendLine(
                "medianBitmapMs=${stageMedians.median("b")} medianObjectMs=${stageMedians.median("o")} " +
                    "medianHandMs=${stageMedians.median("h")} medianWallMs=${stageMedians.median("w")} " +
                    "medianMotionMs=${stageMedians.median("m")} medianLockMs=${stageMedians.median("l")} " +
                    "medianDrawMs=${stageMedians.median("draw")}",
            )
            report.appendLine(
                "stageNote=bitmap is the scale step. yuv is not in this replay. wall is model time and is lower than object+hand when the models overlap.",
            )
            report.appendLine(
                "runningMode=VIDEO numHands=${VisionPipeline.HAND_COUNT} handLongSide=${VisionPipeline.HAND_FRAME} " +
                    "overlap=${pipeline.overlapModels}",
            )
            report.appendLine("videoMode=production RunningMode.VIDEO with monotonic timestamps")
            report.appendLine("gpuNote=one GPU attempt on the production pipeline. Probes below stay on CPU.")
            report.appendLine("objectFrames=$objectFrames invalidFrames=$invalidFrames recovered=$recovered")
            report.appendLine("droppedFrames=$invalidFrames")
            val runtime = Runtime.getRuntime()
            val usedMb = (runtime.totalMemory() - runtime.freeMemory()) / (1024 * 1024)
            report.appendLine("heapUsedMb=$usedMb")
            report.appendLine("thermalSoak=not-run needs-phone")
            report.appendLine("frameAgeNote=lastObjectMs and lastHandMs are emulator detect latency, not phone frame age")
            save()
            try {
                recordHandProbes(context, base, pointed.trackId, report)
                recordSequentialProbe(context, base, report)
            } catch (error: Exception) {
                report.appendLine("probeError=${error.javaClass.simpleName}")
            }
            save()
            assertTrue("sustained run was shorter than 60s\n$report", elapsed >= 60_000)
            assertTrue("object detector stopped\n$report", objectCalls >= 2 && objectFrames >= 2)
            assertTrue("hand landmarker stopped\n$report", handCalls >= 2)
            assertTrue("pipeline did not recover from an invalid frame\n$report", invalidFrames >= 1 && recovered >= 1)
        } finally {
            save()
            pipeline.close()
        }
    }

    /**
     * Real Hand Landmarker on people.jpg. The overlay must keep the whole hand
     * and paint the index fingertip, not only the locked object box.
     */
    @Test(timeout = 180_000)
    fun realPhotoTracksTheHandByTheIndexTip() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val photo = instrumentation.context.assets.open("people.jpg").use { BitmapFactory.decodeStream(it) }
        assertTrue("people.jpg did not decode", photo != null && photo.width > 0)
        val pipeline = VisionPipeline(
            context,
            includeFaces = false,
            preset = DetectionPreset.FAST,
            includeHands = true,
        )
        val report = StringBuilder()
        try {
            val first = pipeline.detect(photo)
            val hands = first.boxes.filter { it.kind == "hand" && it.points.size >= 21 }
            report.appendLine("hands=${hands.size} objects=${first.boxes.count { it.kind == "object" }}")
            assertTrue("Hand Landmarker returned no 21-point hand\n$report", hands.isNotEmpty())
            val tip = HandTrack.indexTip(hands.first())
            assertTrue("index fingertip missing\n$report", tip != null)

            val shifted = shift(photo, 36)
            val second = try {
                pipeline.detect(shifted)
            } finally {
                shifted.recycle()
            }
            val moved = second.boxes.filter { it.kind == "hand" && it.points.size >= 21 }
            report.appendLine("movedHands=${moved.size} firstId=${hands.first().trackId} movedId=${moved.firstOrNull()?.trackId}")
            assertTrue("hand was lost after the photo shifted\n$report", moved.isNotEmpty())
            assertEquals(hands.first().trackId, moved.first().trackId)

            val blank = Bitmap.createBitmap(photo.width, photo.height, Bitmap.Config.ARGB_8888)
            val missed = try {
                pipeline.detect(blank)
            } finally {
                blank.recycle()
            }
            val held = missed.boxes.filter { it.kind == "hand" }
            report.appendLine("heldAfterMiss=${held.size} held=${held.firstOrNull()?.held}")
            val recovered = pipeline.detect(photo)
            val back = recovered.boxes.filter { it.kind == "hand" && !it.held }
            report.appendLine("recovered=${back.map { it.trackId }}")
            assertTrue("hand did not recover after a brief miss\n$report", back.any { it.trackId == hands.first().trackId })

            val view = TrackingOverlayView(context)
            view.measure(
                android.view.View.MeasureSpec.makeMeasureSpec(960, android.view.View.MeasureSpec.EXACTLY),
                android.view.View.MeasureSpec.makeMeasureSpec(540, android.view.View.MeasureSpec.EXACTLY),
            )
            view.layout(0, 0, 960, 540)
            view.updateFrame(first.copy(sequence = 1, capturedAtMs = 1))
            val painted = Bitmap.createBitmap(960, 540, Bitmap.Config.ARGB_8888)
            view.draw(Canvas(painted))
            val cyan = countCyan(painted)
            File(context.filesDir, "hand-track-stage.txt").writeText(report.toString() + "cyan=$cyan drawn=${view.drawnBoxCount()}\n")
            File(context.filesDir, "hand-track-overlay.png").outputStream().use {
                painted.compress(Bitmap.CompressFormat.PNG, 100, it)
            }
            assertTrue("index fingertip marker was not painted\n$report cyan=$cyan", cyan > 10)
            assertTrue("overlay did not keep the hand\n$report", view.drawnBoxCount() >= 1)
            painted.recycle()
        } finally {
            pipeline.close()
        }
    }

    private fun acceptedSwitch(decision: AimDecision, frame: FrameDetections, previousId: Int): Boolean =
        decision.lockedId != null && decision.lockedId != previousId &&
            frame.boxes.any { it.kind == "object" && it.trackId == decision.lockedId }

    private fun recordHandProbes(
        context: android.content.Context,
        base: Bitmap,
        productionTarget: Int,
        report: StringBuilder,
    ) {
        val configs = listOf(
            2 to 640,
            1 to 640,
            2 to 384,
            2 to 320,
        )
        for ((hands, side) in configs) {
            val probe = VisionPipeline(
                context,
                includeFaces = false,
                preset = DetectionPreset.FAST,
                includeHands = true,
                handLongSide = side,
                numHands = hands,
                attemptGpu = false,
                overlapModels = true,
            )
            try {
                val frame = probe.detect(base)
                val ray = IndexPoint.ray(frame)
                val target = if (ray == null) null else IndexPoint.alongRay(ray, frame.boxes, null)?.trackId
                val center = frame.boxes.filter { it.kind == "object" && it.trackId > 0 }.minByOrNull { box ->
                    val cx = (box.left + box.right) / (2f * frame.width) - 0.5f
                    val cy = (box.top + box.bottom) / (2f * frame.height) - 0.5f
                    cx * cx + cy * cy
                }?.trackId
                report.appendLine(
                    "probe hands=$hands side=$side handMs=${probe.lastHandMs} wallMs=${probe.lastWallMs} " +
                        "bitmapMs=${probe.lastBitmapMs} ray=${ray != null} joints=${ray?.joints?.size ?: 0} " +
                        "target=$target center=$center samePerson=${target == productionTarget} " +
                        "delegate=${probe.handDelegate}",
                )
            } catch (error: Exception) {
                report.appendLine("probe hands=$hands side=$side error=${error.javaClass.simpleName}")
            } finally {
                probe.close()
            }
        }
        report.appendLine(
            "productionHandLongSide=${VisionPipeline.HAND_FRAME} numHands=${VisionPipeline.HAND_COUNT} runningMode=VIDEO",
        )
        report.appendLine(
            "handSizeNote=probes are one CPU detect each. Production stays at ${VisionPipeline.HAND_FRAME}px and " +
                "${VisionPipeline.HAND_COUNT} hands unless a probe is faster and target stays person #$productionTarget.",
        )
    }

    private fun recordSequentialProbe(context: android.content.Context, base: Bitmap, report: StringBuilder) {
        val sequential = VisionPipeline(
            context,
            includeFaces = false,
            preset = DetectionPreset.FAST,
            includeHands = true,
            attemptGpu = false,
            overlapModels = false,
        )
        try {
            sequential.detect(base)
            val started = android.os.SystemClock.elapsedRealtime()
            sequential.detect(base)
            val wall = android.os.SystemClock.elapsedRealtime() - started
            report.appendLine(
                "sequentialProbeWallMs=$wall sequentialObjectMs=${sequential.lastObjectMs} " +
                    "sequentialHandMs=${sequential.lastHandMs} sequentialModelWallMs=${sequential.lastWallMs}",
            )
            report.appendLine(
                "overlapNote=production detect overlaps the hand thread with EfficientDet. " +
                    "The sequential probe waits for the hand before the object model. Kept overlap so a hand publish does not wait.",
            )
        } catch (error: Exception) {
            report.appendLine("sequentialProbeError=${error.javaClass.simpleName}")
        } finally {
            sequential.close()
        }
    }

    private fun stepReplayMotion(
        motion: MotionTracker,
        result: FrameDetections,
        steps: Int,
        ready: Boolean,
    ): Boolean {
        val gray = replayGray(steps)
        if (!ready) {
            motion.reset(result, gray)
            return false
        }
        val before = result.boxes.firstOrNull { it.kind == "object" }?.left
        val stepped = motion.step(gray, steps.toLong()).frame
        val after = stepped?.boxes?.firstOrNull { it.kind == "object" }?.left
        return before != null && after != null && kotlin.math.abs(after - before) > 0.5f
    }

    private fun replayGray(step: Int): GrayFrame {
        val width = 64
        val height = 36
        val bytes = ByteArray(width * height)
        val shift = step % 5
        for (y in 0 until height) {
            for (x in 0 until width) {
                val stripe = ((x + shift) / 4 + y / 4) % 2
                bytes[y * width + x] = (if (stripe == 0) 30 else 220).toByte()
            }
        }
        return GrayFrame(width, height, bytes, sequence = step.toLong(), capturedAtMs = step.toLong())
    }

    private fun cover(source: Bitmap, box: BoxObservation, frameWidth: Int, frameHeight: Int): Bitmap {
        val out = source.copy(Bitmap.Config.ARGB_8888, true)
        val canvas = Canvas(out)
        val paint = android.graphics.Paint().apply { color = android.graphics.Color.DKGRAY }
        val sx = source.width.toFloat() / frameWidth
        val sy = source.height.toFloat() / frameHeight
        canvas.drawRect(
            box.left * sx - 12f,
            box.top * sy - 12f,
            box.right * sx + 12f,
            box.bottom * sy + 12f,
            paint,
        )
        return out
    }

    private fun whitePixelsOnBox(bitmap: Bitmap, box: ProjectedBox): Int {
        var count = 0
        val left = box.left.toInt().coerceIn(0, bitmap.width - 1)
        val right = box.right.toInt().coerceIn(0, bitmap.width - 1)
        val top = box.top.toInt().coerceIn(0, bitmap.height - 1)
        val bottom = box.bottom.toInt().coerceIn(0, bitmap.height - 1)
        for (y in top..bottom) {
            for (x in left..right) {
                val pixel = bitmap.getPixel(x, y)
                val red = android.graphics.Color.red(pixel)
                val green = android.graphics.Color.green(pixel)
                val blue = android.graphics.Color.blue(pixel)
                if (red > 220 && green > 220 && blue > 220) count++
            }
        }
        return count
    }

    private fun countCyan(bitmap: Bitmap): Int {
        var count = 0
        for (y in 0 until bitmap.height) {
            for (x in 0 until bitmap.width) {
                val pixel = bitmap.getPixel(x, y)
                val red = android.graphics.Color.red(pixel)
                val green = android.graphics.Color.green(pixel)
                val blue = android.graphics.Color.blue(pixel)
                if (blue > 180 && green > 140 && red < 80) count++
            }
        }
        return count
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
