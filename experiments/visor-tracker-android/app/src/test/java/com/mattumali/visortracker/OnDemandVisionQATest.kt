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
}
