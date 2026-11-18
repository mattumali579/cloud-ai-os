package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class LabelPolicyTest {
    @Test fun scoresUnderTheFloorAreOmitted() {
        assertEquals("object", LabelPolicy.resolve(listOf(CategoryScore("cup", 0.25f))))
        assertNull(LabelPolicy.resolve(listOf(CategoryScore("laptop", 0.20f))))
    }

    @Test fun aScoreTheFastDetectorReturnsIsNotDropped() {
        val shown = LabelPolicy.selection(listOf(
            CategoryScore("", Float.NaN),
            CategoryScore("chair", DetectionPreset.FAST.scoreThreshold),
        ))
        assertNotNull(shown)
        assertEquals("object", shown!!.name)
        assertEquals(DetectionPreset.FAST.scoreThreshold, shown.score, 0.001f)
        assertNotNull(LabelPolicy.resolve(listOf(CategoryScore("person", 0.26f))))
    }

    @Test fun aBlankOrNanScoreCannotReplaceTheRealCategoryScore() {
        val shown = LabelPolicy.selection(listOf(
            CategoryScore("", 0.99f),
            CategoryScore("chair", 0.66f),
        ))
        assertEquals("chair", shown?.name)
        assertEquals(0.66f, shown!!.score, 0.001f)
    }

    @Test fun confidentPhoneStaysPhone() {
        assertEquals("cell phone", LabelPolicy.resolve(listOf(
            CategoryScore("cell phone", 0.81f),
            CategoryScore("laptop", 0.40f),
        )))
    }

    @Test fun phoneAndLaptopNearTieIsUncertain() {
        assertEquals("object", LabelPolicy.resolve(listOf(
            CategoryScore("laptop", 0.70f),
            CategoryScore("cell phone", 0.58f),
        )))
    }

    @Test fun weakLaptopIsUncertainInsteadOfAFalseName() {
        assertEquals("object", LabelPolicy.resolve(listOf(CategoryScore("laptop", 0.50f))))
    }

    @Test fun moderateScissorsIsUncertainRatherThanAConfidentFalseName() {
        // COCO has no glasses class. Lite0 calling eyewear "scissors" shows up
        // as a middling scissors score; that must not be drawn as scissors.
        assertEquals("object", LabelPolicy.resolve(listOf(CategoryScore("scissors", 0.55f))))
    }

    @Test fun highConfidenceScissorsCanStillBeScissors() {
        assertEquals("scissors", LabelPolicy.resolve(listOf(CategoryScore("scissors", 0.86f))))
    }

    @Test fun moderateScissorsDoesNotRenameATrackedPhone() {
        val tracker = TrackIds()
        val phone = BoxObservation("object", "cell phone", .9f, 10f, 10f, 70f, 90f)
        tracker.update(listOf(phone))
        val uncertain = LabelPolicy.resolve(listOf(CategoryScore("scissors", 0.55f)))
        val shown = tracker.update(listOf(
            BoxObservation("object", uncertain!!, .55f, 12f, 12f, 72f, 92f)
        ))
        assertEquals("cell phone", shown.first().label)
        assertEquals(phone.trackId, shown.first().trackId)
    }
}
