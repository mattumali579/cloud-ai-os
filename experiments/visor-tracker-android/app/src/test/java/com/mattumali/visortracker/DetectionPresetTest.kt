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
        assertTrue(DetectionPreset.FAST.scoreThreshold <= LabelPolicy.MIN_SCORE)
    }

    @Test fun yolo26SegPresetConfiguredCorrectly() {
        assertEquals(DetectionPreset.YOLO26_SEG, DetectionPreset.read("YOLO26_SEG"))
        assertEquals("yolo26n-seg.onnx", DetectionPreset.YOLO26_SEG.modelAsset)
        assertEquals(640, DetectionPreset.YOLO26_SEG.maxFrameWidth)
        assertEquals(0.25f, DetectionPreset.YOLO26_SEG.scoreThreshold, 0.001f)
        assertEquals(8, DetectionPreset.YOLO26_SEG.maxResults)
        assertTrue(DetectionPreset.YOLO26_SEG.isYolo)
        org.junit.Assert.assertFalse(DetectionPreset.FAST.isYolo)
        org.junit.Assert.assertFalse(DetectionPreset.ACCURATE.isYolo)
    }
}
