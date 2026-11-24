package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class OnDemandVisionQATest {

    @Test
    fun cloudVlmRequiresAffirmativeConsent() {
        val qa = OnDemandVisionQA(context = null as android.content.Context?)
        var receivedResult: OnDemandVisionQA.VisualQAResult? = null

        // With cloudConsentGranted = false, must immediately refuse with consent error
        qa.askSceneQuestion(
            bitmap = null as android.graphics.Bitmap?,
            question = "What object is in view?",
            cloudConsentGranted = false,
        ) { result ->
            receivedResult = result
        }

        assertNotNull(receivedResult)
        assertFalse(receivedResult!!.cloudConsentGranted)
        assertTrue(receivedResult!!.error!!.contains("Explicit user authorization required"))
    }

    @Test
    fun groundedLocalizationRequiresAffirmativeConsent() {
        val qa = OnDemandVisionQA(context = null)
        var received: OnDemandVisionQA.GroundedVisionResult? = null
        qa.locatePointedObjects(
            bitmap = null,
            hint = GroundingHint(1, 1080, 1440, 100f, 100f, 1f, 0f, true),
            cloudConsentGranted = false,
        ) { received = it }
        assertNotNull(received)
        assertFalse(received!!.cloudConsentGranted)
        assertTrue(received!!.objects.isEmpty())
    }

    @Test
    fun parserAcceptsGroundedBoxesAndRejectsOversizedOrMalformedOnes() {
        val qa = OnDemandVisionQA(context = null)
        val parsed = qa.parseGroundedObjects(
            """{"objects":[
                {"label":"Conical Hat","box_2d":[100,200,400,500]},
                {"label":"whole room","box_2d":[0,0,1000,1000]},
                {"label":"bad","box_2d":[500,500,400,600]}
            ]}"""
        )
        assertEquals(1, parsed.size)
        assertEquals("conical hat", parsed.single().label)
        val box = parsed.single().toObservation(1000, 2000)
        assertEquals(200f, box.left, 0.01f)
        assertEquals(200f, box.top, 0.01f)
        assertEquals(500f, box.right, 0.01f)
        assertEquals(800f, box.bottom, 0.01f)
        assertEquals(0f, box.score, 0.0f)
    }
}
