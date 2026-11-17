package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DetectionPresetTest {
    @Test fun defaultsToFastOnUnknownPreference() {
        assertEquals(DetectionPreset.FAST, DetectionPreset.read(null))
        assertEquals(DetectionPreset.FAST, DetectionPreset.read("invalid"))
    }

    @Test fun accurateModeUsesHigherResolutionModel() {
        assertEquals(DetectionPreset.ACCURATE, DetectionPreset.read("ACCURATE"))
        assertEquals("efficientdet_lite2.tflite", DetectionPreset.ACCURATE.modelAsset)
        assertTrue(DetectionPreset.ACCURATE.maxFrameWidth > DetectionPreset.FAST.maxFrameWidth)
    }

    @Test fun bothPresetsRejectTheOldQuarterScoreAndTheTenPlusResultBudget() {
        assertEquals(0.25f, DetectionPreset.FAST.scoreThreshold, 0.001f)
        assertTrue(DetectionPreset.ACCURATE.scoreThreshold >= 0.45f)
        assertTrue(DetectionPreset.FAST.maxResults in 5..8)
        assertTrue(DetectionPreset.ACCURATE.maxResults in 5..8)
        assertEquals("efficientdet_lite0.tflite", DetectionPreset.FAST.modelAsset)
    }
}
