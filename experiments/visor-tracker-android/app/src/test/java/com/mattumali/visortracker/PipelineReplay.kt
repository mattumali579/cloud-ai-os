package com.mattumali.visortracker

/**
 * Deterministic replay of fixed frames through the production tracker.
 * No clock sleep, no random numbers, no device, and no emulator.
 *
 * MediaPipe [VisionPipeline.detect] is not called. This JVM has no Android
 * MediaPipe native library, and the unit-test job does not start an emulator.
 * Category scores below are the script, not model output.
 * Visor `camera.live` is not called. That boundary stays unverified.
 */
internal object PipelineReplay {
    enum class Kind { REAL, MOCKED, UNVERIFIED }

    data class Boundary(val component: String, val kind: Kind, val detail: String)

    data class FrameOutcome(
        val sequence: Long,
        val lockId: Int?,
        val publishedTrackId: Int?,
    )

    data class MotionOutcome(
        val firstLeft: Float,
        val movedLeft: Float,
        val firstProjectedLeft: Float,
        val movedProjectedLeft: Float,
        val movedVisible: Boolean,
        val marginVisible: Boolean,
        val disappeared: Boolean,
    )

    data class YuvFault(val scenario: String, val message: String)

    fun boundaries(): List<Boundary> = listOf(
        Boundary(
            "YuvSnapshot.writeArgb",
            Kind.REAL,
            "Fixed YUV bytes are converted by production writeArgb, outputSize, and releasePlanes.",
        ),
        Boundary(
            "LabelPolicy.selection",
            Kind.REAL,
            "Fixed category scores go through production selection. Scores under MIN_SCORE produce no box.",
        ),
        Boundary(
            "VisionPipeline.detect MediaPipe",
            Kind.MOCKED,
            "ObjectDetector, HandLandmarker, and FaceDetector were not called. The host JVM and the unit-test job cannot load libmediapipe_tasks_vision.",
        ),
        Boundary(
            "VisionPipeline.runOptional",
            Kind.REAL,
            "A throwing hand block is caught by production runOptional. Object boxes are not discarded by that helper.",
        ),
        Boundary(
            "OptionalModelFault",
            Kind.REAL,
            "The hand fault string is copied into StageFaults the same way OverlayTrackingService fills the notification.",
        ),
        Boundary(
            "AimLock.pointedObject",
            Kind.REAL,
            "A lock requires a confirmed pointing hand. The picture center is not a finger.",
        ),
        Boundary(
            "OverlayFrameSelection.lockIdForFrame",
            Kind.REAL,
            "The lock is applied, then the id is read, then nothing is drawn on a canvas.",
        ),
        Boundary(
            "TrackIds.update",
            Kind.REAL,
            "Ids in the switch replay come from production overlap matching, not from the script.",
        ),
        Boundary(
            "TrackingHandoff.pump",
            Kind.REAL,
            "Fixed gray bytes are offered to the production handoff, which calls MotionTracker and NearbyCandidates.",
        ),
        Boundary(
            "CoverGeometry.project",
            Kind.REAL,
            "The projected rectangle is the one TrackingOverlayView.onDraw passes to the canvas.",
        ),
        Boundary(
            "TrackingOverlayView.onDraw",
            Kind.MOCKED,
            "No window or Canvas. The harness stops at CoverGeometry.project, which onDraw uses for the box.",
        ),
        Boundary(
            "Visor camera.live",
            Kind.UNVERIFIED,
            "The Ray-Ban stream and Visor Development Kit are not opened. Only this boundary is unverified.",
        ),
    )

    /** White limited-range frame. This is the bitmap-sized input the detector would receive. */
    fun detectorInputSize(): Pair<Int, Int> {
        val width = 16
        val height = 8
        val y = ByteArray(width * height) { 235.toByte() }
        val uv = ByteArray((width / 2) * (height / 2)) { 128.toByte() }
        val rgb = IntArray(width * height)
        val size = YuvSnapshot.writeArgb(
            width, height, 384,
            y, y.size, width, 1, 0, 0,
            uv, uv.size, width / 2, 1, 0, 0,
            uv, uv.size, width / 2, 1, 0, 0,
            rgb,
        )
        check(rgb[0] == 0xffffffff.toInt()) { "decoded detector input was not limited-range white" }
        return size
    }

    fun invalidYuv(): List<YuvFault> {
        val faults = mutableListOf<YuvFault>()
        faults += catchFault("zero-size") { YuvSnapshot.outputSize(0, 8, 384) }
        faults += catchFault("empty") {
            YuvSnapshot.writeArgb(
                2, 2, 2,
                ByteArray(8), 0, 2, 1, 0, 0,
                ByteArray(4) { 128.toByte() }, 4, 2, 1, 0, 0,
                ByteArray(4) { 128.toByte() }, 4, 2, 1, 0, 0,
                IntArray(4),
            )
        }
        faults += catchFault("short") {
            YuvSnapshot.writeArgb(
                4, 2, 4,
                ByteArray(4) { 235.toByte() }, 4, 4, 1, 0, 0,
                ByteArray(4) { 128.toByte() }, 4, 2, 1, 0, 0,
                ByteArray(4) { 128.toByte() }, 4, 2, 1, 0, 0,
                IntArray(8),
            )
        }
        faults += catchFault("overflow") {
            YuvSnapshot.writeArgb(
                2, 2, 2,
                ByteArray(8) { 235.toByte() }, 8, 2_000_000_000, 1, 0, 0,
                ByteArray(4) { 128.toByte() }, 4, 2, 1, 0, 0,
                ByteArray(4) { 128.toByte() }, 4, 2, 1, 0, 0,
                IntArray(4),
            )
        }
        faults += catchFault("use-after-return") {
            val snapshot = YuvSnapshot.retainedForTest(4, 2)
            snapshot.releasePlanes()
            snapshot.toBitmap()
        }
        return faults
    }

    /** Picture-center aim: acquire A, move to B, then back to A. */
    fun switchTargets(): List<FrameOutcome> {
        val tracks = TrackIds()
        val aim = AimLock()
        val handoff = TrackingHandoff()
        val positions = listOf(80, 80) + (76 downTo 40 step 4).toList() + (44..80 step 4).toList()
        return positions.mapIndexed { index, left ->
            val raw = listOf(
                BoxObservation("object", "cup", 0.91f, 20f, 20f, 80f, 80f),
                BoxObservation("object", "book", 0.91f, left.toFloat(), left.toFloat(), left + 20f, left + 20f),
            )
            val labeled = raw.map { box ->
                val chosen = LabelPolicy.selection(listOf(CategoryScore(box.label, box.score)))
                    ?: error("score ${box.score} was dropped")
                box.copy(label = chosen.name, score = chosen.score)
            }
            val visible = tracks.update(labeled).filter { !it.held }
            val sequence = index + 1L
            val at = 10_000L + index * 200L
            val frame = FrameDetections(100, 100, visible, sequence, at)
            val lockId = OverlayFrameSelection.lockIdForFrame(
                applyLock = { aim.observe(frame) },
                readLock = { aim.lockedId },
                deferDraw = { },
            )
            val gray = GrayFrame(100, 100, ByteArray(100 * 100) { 40 }, sequence, at)
            handoff.offer(gray)
            val event = handoff.pump(at + 8, DetectionBaseline(frame, gray), lockId)
            FrameOutcome(sequence, lockId, event?.frame?.boxes?.firstOrNull()?.trackId)
        }
    }

    fun moveThenDisappear(): MotionOutcome {
        val handoff = TrackingHandoff()
        val firstGray = textured(0, 1, 1_000)
        handoff.offer(firstGray)
        val chosen = LabelPolicy.selection(listOf(CategoryScore("cup", 0.9f)))
            ?: error("moving cup was dropped")
        val box = BoxObservation("object", chosen.name, chosen.score, 30f, 30f, 50f, 50f, trackId = 7)
        val detection = FrameDetections(100, 80, listOf(box), 1, 1_000)
        val first = handoff.pump(1_008, DetectionBaseline(detection, firstGray), 7)
            ?: error("first detection published nothing")
        val movedGray = textured(4, 2, 1_033)
        handoff.offer(movedGray)
        val moved = handoff.pump(1_040, null, 7)
            ?: error("motion step published nothing")
        val blank = GrayFrame(100, 80, ByteArray(100 * 80) { 40 }, 3, 1_066)
        handoff.offer(blank)
        val gone = handoff.pump(1_070, null, 7)
        val before = CoverGeometry.project(100, 80, 1080, 1920, first.frame.boxes.single())
        val after = CoverGeometry.project(100, 80, 1080, 1920, moved.frame.boxes.single())
        val margin = BoxObservation("object", "cup", 0.9f, 0f, 30f, 5f, 50f, trackId = 7)
        return MotionOutcome(
            firstLeft = first.frame.boxes.single().left,
            movedLeft = moved.frame.boxes.single().left,
            firstProjectedLeft = before.left,
            movedProjectedLeft = after.left,
            movedVisible = CoverGeometry.isVisible(100, 80, 1080, 1920, moved.frame.boxes.single()),
            marginVisible = CoverGeometry.isVisible(100, 80, 1080, 1920, margin),
            disappeared = gone == null,
        )
    }

    /** A locked target that leaves the frame must not be replaced by the other object on that detection. */
    fun missingLockIsNotStolen(): Int? {
        val handoff = TrackingHandoff()
        val gray = textured(0, 1, 2_000)
        handoff.offer(gray)
        val locked = BoxObservation("object", "cup", 0.9f, 30f, 30f, 50f, 50f, trackId = 4)
        val first = FrameDetections(100, 80, listOf(locked), 1, 2_000)
        handoff.pump(2_008, DetectionBaseline(first, gray), 4)
            ?: error("lock was not published")
        val nextGray = textured(0, 2, 2_033)
        handoff.offer(nextGray)
        val stranger = BoxObservation("object", "book", 0.9f, 0f, 0f, 12f, 12f, trackId = 9)
        val second = FrameDetections(100, 80, listOf(stranger), 2, 2_033)
        val event = handoff.pump(2_040, DetectionBaseline(second, nextGray), 4)
        return event?.frame?.boxes?.firstOrNull()?.trackId
    }

    fun handFaultNotification(): String {
        var fault: String? = null
        val objects = listOf(BoxObservation("object", "cup", 0.9f, 30f, 30f, 50f, 50f, trackId = 7))
        val kept = VisionPipeline.runOptional("hands", { fault = it }) { objects }
        check(kept == objects) { "a successful optional pass dropped the object boxes" }
        val failed = VisionPipeline.runOptional("hands", { fault = it }) { error("model hung") }
        check(failed.isEmpty())
        val stages = StageFaults()
        OptionalModelFault.apply(stages, fault)
        return OptionalModelFault.notificationExtra("V30", "", stages.summary())
    }

    private fun catchFault(scenario: String, block: () -> Unit): YuvFault {
        return try {
            block()
            YuvFault(scenario, "")
        } catch (error: RuntimeException) {
            YuvFault(scenario, error.message ?: error.javaClass.simpleName)
        }
    }

    /** Same textured patch MotionTracker follows. The shift is a fixed byte offset, not a random draw. */
    private fun textured(dx: Int, sequence: Long, at: Long): GrayFrame {
        val width = 100
        val height = 80
        val pixels = ByteArray(width * height) { 25 }
        for (y in 30 until 50) {
            for (x in 30 + dx until 50 + dx) {
                pixels[y * width + x] =
                    (65 + ((x - dx) * 37 + y * 19 + (x - dx) * (y + 11) * 7) % 175).toByte()
            }
        }
        return GrayFrame(width, height, pixels, sequence, at)
    }
}
