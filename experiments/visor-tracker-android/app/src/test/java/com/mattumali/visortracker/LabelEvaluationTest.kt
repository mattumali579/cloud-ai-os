package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Category-score fixtures, not a camera dataset.
 * A score floor is not a measured accuracy rate. Lite2 is not compared here
 * because this test does not run either model.
 */
class LabelEvaluationTest {
    private data class Sample(
        val truth: String,
        val categories: List<CategoryScore>,
        val correctable: Boolean,
    )

    private val samples = listOf(
        Sample("cell phone", listOf(CategoryScore("cell phone", 0.81f), CategoryScore("laptop", 0.20f)), true),
        Sample("cell phone", listOf(CategoryScore("laptop", 0.70f), CategoryScore("cell phone", 0.58f)), true),
        Sample("cell phone", listOf(CategoryScore("laptop", 0.50f)), true),
        Sample("glasses", listOf(CategoryScore("scissors", 0.55f)), true),
        Sample("glasses", listOf(CategoryScore("scissors", 0.90f)), false),
        Sample("chair", listOf(CategoryScore("chair", 0.66f)), true),
        Sample("cup", listOf(CategoryScore("cup", 0.30f)), true),
        Sample("scissors", listOf(CategoryScore("scissors", 0.90f)), true),
        Sample("cell phone", listOf(CategoryScore("cell phone", 0.56f), CategoryScore("laptop", 0.50f)), true),
        Sample("book", listOf(CategoryScore("book", 0.80f)), true),
    )

    @Test fun correctableFixturesNeverEmitADifferentSpecificName() {
        var specificCorrect = 0
        var withheld = 0
        var omitted = 0
        var falseSpecific = 0
        for (sample in samples.filter { it.correctable }) {
            val shown = LabelPolicy.resolve(sample.categories)
            when {
                shown == null -> omitted++
                shown == LabelPolicy.UNCERTAIN -> withheld++
                shown == sample.truth -> specificCorrect++
                else -> falseSpecific++
            }
        }
        val uncorrectable = samples.filter { !it.correctable }.map { sample ->
            sample.truth to LabelPolicy.resolve(sample.categories)
        }
        println(
            "label fixtures: specificCorrect=$specificCorrect withheld=$withheld " +
                "omitted=$omitted falseSpecific=$falseSpecific uncorrectable=$uncorrectable"
        )
        assertEquals(0, falseSpecific)
        assertTrue(specificCorrect >= 3)
        assertTrue(withheld >= 3)
        // COCO has no glasses class. A high scissors score is still scissors.
        assertEquals("scissors", uncorrectable.single().second)
    }

    @Test fun phoneCalledLaptopAndGlassesCalledScissorsStayUncertainWhenTheScoreIsMiddling() {
        assertEquals("object", LabelPolicy.resolve(listOf(
            CategoryScore("laptop", 0.70f),
            CategoryScore("cell phone", 0.58f),
        )))
        assertEquals("object", LabelPolicy.resolve(listOf(CategoryScore("scissors", 0.55f))))
        assertNull(LabelPolicy.resolve(listOf(CategoryScore("laptop", 0.25f))))
    }

    @Test fun uncertainLaptopFramesDoNotRenameAPhone() {
        val tracker = TrackIds()
        val phone = BoxObservation("object", "cell phone", 0.9f, 10f, 10f, 70f, 90f)
        tracker.update(listOf(phone))
        repeat(4) {
            val label = LabelPolicy.resolve(listOf(CategoryScore("laptop", 0.55f)))
            assertEquals("object", label)
            val shown = tracker.update(listOf(
                BoxObservation("object", label!!, 0.55f, 12f, 12f, 72f, 92f)
            ))
            assertEquals("cell phone", shown.first().label)
            assertEquals(phone.trackId, shown.first().trackId)
        }
    }
}
