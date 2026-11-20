package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TrackerReleaseTest {
    @Test fun apiJsonPicksTheHighestTrackerTag() {
        val raw = """
            [{"tag_name":"visor-tracker-v31","assets":[]},
             {"tag_name":"other-v99"},
             {"tag_name":"visor-tracker-v32"}]
        """.trimIndent()
        val found = TrackerRelease.newestFromApiJson(raw)!!
        assertEquals(32L, found.version)
        assertEquals(TrackerRelease.downloadUrl(32), found.url)
    }

    @Test fun fallbackLooksAheadWhenTheApiIsDown() {
        assertEquals(listOf(33L, 34L, 35L), TrackerRelease.fallbackVersions(32))
        assertTrue(TrackerRelease.downloadUrl(33).endsWith("/visor-tracker-v33/Visor-Fast-Tracking.apk"))
        assertEquals("https://github.com/mattumali579/cloud-ai-os/releases", TrackerRelease.RELEASES_PAGE)
    }
}
