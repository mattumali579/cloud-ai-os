package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Test

class YuvSnapshotTest {
    @Test fun limitedRangeBlackIsBlack() {
        assertEquals(0xff000000.toInt(), YuvSnapshot.yuvToArgb(16, 128, 128))
    }

    @Test fun limitedRangeWhiteIsWhite() {
        assertEquals(0xffffffff.toInt(), YuvSnapshot.yuvToArgb(235, 128, 128))
    }

    @Test fun redChannelIsClamped() {
        val pixel = YuvSnapshot.yuvToArgb(81, 90, 240)
        val r = (pixel shr 16) and 255
        val g = (pixel shr 8) and 255
        val b = pixel and 255
        assertEquals(true, r > g && r > b)
    }
}
