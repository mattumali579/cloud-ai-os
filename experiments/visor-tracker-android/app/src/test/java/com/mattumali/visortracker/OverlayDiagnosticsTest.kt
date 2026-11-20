package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OverlayDiagnosticsTest {
    private fun live(edit: OverlaySnapshot.() -> OverlaySnapshot = { this }) = OverlaySnapshot(
        versionName = "0.14.0",
        versionCode = 14,
        packageName = "com.mattumali.visortracker.stable",
        overlayPermission = true,
        serviceRunning = true,
        overlayAttached = true,
        cameraGranted = true,
        liveConnected = true,
        framesReceived = 30,
        grayFrames = 30,
        inferencesCompleted = 4,
        rawDetections = 0,
        freshBoxes = 0,
    ).edit()

    @Test fun overlayPermissionBeatsAMissingStream() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(overlayPermission = false, framesReceived = 0, liveConnected = false, cameraGranted = null)
        })
        assertEquals(OverlayHop.OVERLAY_BLOCKED, diagnosis.hop)
        assertEquals(OverlayDiagnostics.CODE_BLOCKED, diagnosis.code)
    }

    @Test fun addViewFailureStaysBlockedAfterTheServiceStops() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(serviceRunning = false, overlayAttached = false, overlayError = "BadTokenException")
        })
        assertEquals(OverlayHop.OVERLAY_BLOCKED, diagnosis.hop)
        assertTrue(diagnosis.detail.contains("BadTokenException"))
    }

    @Test fun runningWithoutAWindowIsBlocked() {
        val diagnosis = OverlayDiagnostics.diagnose(live { copy(overlayAttached = false) })
        assertEquals(OverlayHop.OVERLAY_BLOCKED, diagnosis.hop)
    }

    @Test fun stoppedServiceIsNotCalledACameraFailure() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(serviceRunning = false, overlayAttached = false, framesReceived = 0)
        })
        assertEquals(OverlayHop.SERVICE_STOPPED, diagnosis.hop)
    }

    @Test fun cameraDenialBeatsNoFrames() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(cameraGranted = false, liveConnected = false, framesReceived = 0)
        })
        assertEquals(OverlayHop.CAMERA_DENIED, diagnosis.hop)
    }

    @Test fun mediaPipeFailureIsNotNoCameraFrames() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(
                cameraGranted = null,
                liveConnected = false,
                framesReceived = 0,
                inferencesCompleted = 0,
                failure = "MediaPipe failed: model missing",
            )
        })
        assertEquals(OverlayHop.DETECTOR_FAILED, diagnosis.hop)
        assertTrue(diagnosis.detail.contains("model missing"))
    }

    @Test fun detectorStageFaultWithoutMediaPipePrefixIsDetectorFailure() {
        val failure = "detect: Decode/detection failed: model hung"
        assertEquals(false, failure.startsWith("MediaPipe"))
        val diagnosis = OverlayDiagnostics.diagnose(live { copy(failure = failure) })
        assertEquals(OverlayHop.DETECTOR_FAILED, diagnosis.hop)
        assertEquals("DETECTOR FAILED", diagnosis.code)
        assertEquals(failure, diagnosis.detail)
    }

    @Test fun optionalHandOrFaceFaultIsNotAStreamFailure() {
        for (failure in listOf("hands: model hung", "faces: model hung")) {
            val diagnosis = OverlayDiagnostics.diagnose(live { copy(failure = failure) })
            assertEquals(OverlayHop.DETECTOR_FAILED, diagnosis.hop)
            assertEquals("DETECTOR FAILED", diagnosis.code)
            assertEquals(failure, diagnosis.detail)
        }
    }

    @Test fun streamExceptionIsShownWhenNoFrameArrived() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(framesReceived = 0, inferencesCompleted = 0, failure = "VDK stream failed: wifi_off")
        })
        assertEquals(OverlayHop.STREAM_FAILED, diagnosis.hop)
        assertTrue(diagnosis.detail.contains("wifi_off"))
    }

    @Test fun connectedDecoderWithZeroFramesIsNoCameraFrames() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(framesReceived = 0, inferencesCompleted = 0)
        })
        assertEquals(OverlayHop.NO_CAMERA_FRAMES, diagnosis.hop)
        assertEquals(OverlayDiagnostics.CODE_NO_FRAMES, diagnosis.code)
    }

    @Test fun aRunningFirstPassIsNotZeroObjects() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(inferencesCompleted = 0, rawDetections = 0, detectorBusy = true)
        })
        assertEquals(OverlayHop.DETECTOR_RUNNING, diagnosis.hop)
    }

    @Test fun framesBeforeTheFirstInferenceAreNotZeroObjects() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(inferencesCompleted = 0, rawDetections = 0, freshBoxes = 0)
        })
        assertEquals(OverlayHop.WAITING_FOR_DETECTOR, diagnosis.hop)
    }

    @Test fun finishedInferenceWithNothingToDrawIsZeroObjects() {
        val diagnosis = OverlayDiagnostics.diagnose(live())
        assertEquals(OverlayHop.ZERO_OBJECTS, diagnosis.hop)
        assertEquals(OverlayDiagnostics.CODE_ZERO, diagnosis.code)
    }

    @Test fun unmatchedDetectionsAreHiddenRatherThanAbsent() {
        val diagnosis = OverlayDiagnostics.diagnose(live { copy(rawDetections = 2, freshBoxes = 0) })
        assertEquals(OverlayHop.BOXES_HIDDEN, diagnosis.hop)
    }

    @Test fun visibleBoxesWinOverAnOldFailure() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(freshBoxes = 1, drawnBoxes = 1, boxAgeMs = 40, failure = "VDK stream failed: stopped")
        })
        assertEquals(OverlayHop.TRACKING, diagnosis.hop)
        assertTrue(diagnosis.detail.contains("40"))
    }

    @Test fun aPublishedBoxThatWasNotPaintedIsHidden() {
        val diagnosis = OverlayDiagnostics.diagnose(live {
            copy(freshBoxes = 1, drawnBoxes = 0, rawDetections = 1, failure = "VDK stream failed: stopped")
        })
        assertEquals(OverlayHop.BOXES_HIDDEN, diagnosis.hop)
        assertTrue(diagnosis.detail.contains("not painting"))
    }

    @Test fun proofMarkNamesTheVersionAndTheHop() {
        val snapshot = live { copy(framesReceived = 0, inferencesCompleted = 0, decoderFps = 0f) }
        val diagnosis = OverlayDiagnostics.diagnose(snapshot)
        val lines = OverlayDiagnostics.probeLines(snapshot, diagnosis)
        assertEquals(listOf("v0.14.0 #14", OverlayDiagnostics.CODE_NO_FRAMES, "det0 pub0 drw0"), lines)
    }

    @Test fun proofMarkCanBeTurnedOff() {
        val snapshot = live { copy(probeEnabled = false, freshBoxes = 2, drawnBoxes = 2) }
        val diagnosis = OverlayDiagnostics.diagnose(snapshot)
        assertTrue(OverlayDiagnostics.probeLines(snapshot, diagnosis).isEmpty())
        val line = OverlayDiagnostics.notificationLine(snapshot, diagnosis, "V30")
        assertTrue(line.contains("com.mattumali.visortracker.stable"))
        assertTrue(line.contains("0.14.0 (14)"))
        assertTrue(line.contains("TRACKING"))
        assertTrue(line.endsWith("V30"))
    }

    @Test fun connectingIsDistinctFromASilentStream() {
        val connecting = OverlayDiagnostics.diagnose(live {
            copy(cameraGranted = null, liveConnected = false, framesReceived = 0, inferencesCompleted = 0)
        })
        assertEquals(OverlayHop.CONNECTING, connecting.hop)
    }

    @Test fun aNewerFrameIsDrawnAndAnOlderClearIsNot() {
        assertTrue(OverlayDiagnostics.acceptFrame(8, 7, incomingBoxes = 1, showingTrackId = 1, incomingTrackId = 1, lockedId = 1, captureIsRecent = true))
        assertTrue(OverlayDiagnostics.acceptFrame(8, 7, incomingBoxes = 0, showingTrackId = 1, incomingTrackId = null, lockedId = 1, captureIsRecent = true))
        assertEquals(false, OverlayDiagnostics.acceptFrame(6, 7, incomingBoxes = 0, showingTrackId = 1, incomingTrackId = null, lockedId = 1, captureIsRecent = true))
    }

    @Test fun aRecentDetectionFillsABlankOverlayEvenWhenItsSequenceIsOlder() {
        assertTrue(OverlayDiagnostics.acceptFrame(4, 9, incomingBoxes = 1, showingTrackId = null, incomingTrackId = 3, lockedId = 3, captureIsRecent = true))
        assertEquals(false, OverlayDiagnostics.acceptFrame(4, 9, incomingBoxes = 1, showingTrackId = null, incomingTrackId = 3, lockedId = 3, captureIsRecent = false))
    }

    @Test fun aStaleFrameDoesNotMoveTheLockButCanReplaceTheWrongBox() {
        assertEquals(false, OverlayDiagnostics.acceptFrame(4, 9, incomingBoxes = 1, showingTrackId = 3, incomingTrackId = 3, lockedId = 3, captureIsRecent = true))
        assertTrue(OverlayDiagnostics.acceptFrame(4, 9, incomingBoxes = 1, showingTrackId = 2, incomingTrackId = 3, lockedId = 3, captureIsRecent = true))
        assertEquals(false, OverlayDiagnostics.acceptFrame(4, 9, incomingBoxes = 1, showingTrackId = 3, incomingTrackId = 8, lockedId = 3, captureIsRecent = true))
    }

    @Test fun shownSequenceDoesNotMoveBackwardWhenAStaleDetectionIsAccepted() {
        assertEquals(9L, OverlayDiagnostics.nextShownSequence(9, 4, accepted = true))
        assertEquals(10L, OverlayDiagnostics.nextShownSequence(9, 10, accepted = true))
        assertEquals(9L, OverlayDiagnostics.nextShownSequence(9, 12, accepted = false))
    }

    @Test fun heldBoxesKeepTheFrameTheyWereMeasuredIn() {
        val box = BoxObservation("object", "cup", 0.9f, 412f, 284f, 612f, 484f, trackId = 3)
        val previous = FrameDetections(1024, 768, listOf(box), sequence = 4, capturedAtMs = 1_000)
        val candidate = FrameDetections(192, 144, emptyList(), sequence = 9, capturedAtMs = 1_200)
        val drawn = OverlayDiagnostics.frameToDraw(candidate, previous, incomingBoxes = 0, kept = listOf(box))
        assertEquals(1024, drawn.width)
        assertEquals(768, drawn.height)
        assertEquals(1_000L, drawn.capturedAtMs)
        assertEquals(3, drawn.boxes.single().trackId)
        assertTrue(CoverGeometry.isVisible(drawn.width, drawn.height, 1080, 1920, drawn.boxes.single()))
        assertEquals(false, CoverGeometry.isVisible(candidate.width, candidate.height, 1080, 1920, box))
    }

    @Test fun aNewDetectionUsesItsOwnFrameSize() {
        val previousBox = BoxObservation("object", "cup", 0.9f, 10f, 10f, 40f, 80f, trackId = 3)
        val next = previousBox.copy(left = 20f, right = 50f)
        val previous = FrameDetections(640, 480, listOf(previousBox), sequence = 4, capturedAtMs = 1_000)
        val candidate = FrameDetections(320, 240, listOf(next), sequence = 5, capturedAtMs = 1_100)
        val drawn = OverlayDiagnostics.frameToDraw(candidate, previous, incomingBoxes = 1, kept = listOf(next))
        assertEquals(320, drawn.width)
        assertEquals(240, drawn.height)
        assertEquals(1_100L, drawn.capturedAtMs)
        assertEquals(20f, drawn.boxes.single().left, 0.01f)
    }

    @Test fun ageDoesNotWrapAndAMissingClockDoesNotLookStale() {
        assertEquals(60, OverlayDiagnostics.ageMs(100, 40))
        assertEquals(0, OverlayDiagnostics.ageMs(40, 100))
        assertEquals(0, OverlayDiagnostics.ageMs(0, 10))
        assertEquals(Int.MAX_VALUE, OverlayDiagnostics.ageMs(Int.MAX_VALUE.toLong() + 80, 1))
        assertTrue(OverlayDiagnostics.captureIsRecent(5_000, 4_000, 2_000))
        assertEquals(false, OverlayDiagnostics.captureIsRecent(5_000, 1_000, 2_000))
        assertTrue(OverlayDiagnostics.captureIsRecent(5_000, 0, 2_000))
    }

    @Test fun onlyARealObjectIsPaintable() {
        val objectBox = BoxObservation("object", "cup", 0.9f, 10f, 10f, 40f, 80f, trackId = 3)
        val held = objectBox.copy(held = true)
        val hand = BoxObservation("hand", "hand", 1f, 0f, 0f, 10f, 10f, trackId = 1)
        assertTrue(OverlayDiagnostics.hasPaintableObject(listOf(hand, objectBox)))
        assertEquals(false, OverlayDiagnostics.hasPaintableObject(listOf(held, hand)))
    }
}
