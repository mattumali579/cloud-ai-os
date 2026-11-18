package com.mattumali.visortracker

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TrackerLogTest {
    @Test fun linesStayOnDiskAfterTheWriterIsGone() {
        val directory = File("build/tracker-log-test").apply {
            deleteRecursively()
            mkdirs()
        }
        TrackerLog.record(directory, 1_000L, "detect", "objects=2 seq=9")
        TrackerLog.record(directory, 2_000L, "publish", "boxes=1 seq=9")
        val text = TrackerLog.read(directory)
        assertTrue(text.contains("1000 | detect | objects=2 seq=9"))
        assertTrue(text.contains("2000 | publish | boxes=1 seq=9"))
        assertEquals(text, TrackerLog.read(directory))
        directory.deleteRecursively()
    }
}
