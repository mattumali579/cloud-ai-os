package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class GroundedTargetingFallbackTest {
    private fun hint(sequence: Long, eligible: Boolean = true, dirX: Float = 1f, dirY: Float = 0f) =
        GroundingHint(sequence, 1080, 1440, 300f, 700f, dirX, dirY, eligible)

    @Test
    fun requiresSustainedEligiblePointAndOnlyRequestsOncePerEpisode() {
        val gate = GroundingRequestGate(stableFramesRequired = 3, cooldownMs = 0)
        assertFalse(gate.shouldRequest(hint(1), 1))
        assertFalse(gate.shouldRequest(hint(2), 2))
        assertTrue(gate.shouldRequest(hint(3), 3))
        gate.complete()
        assertFalse(gate.shouldRequest(hint(4), 4))
        assertFalse(gate.shouldRequest(hint(5, eligible = false), 5))
        assertFalse(gate.shouldRequest(hint(6), 6))
        assertFalse(gate.shouldRequest(hint(7), 7))
        assertTrue(gate.shouldRequest(hint(8), 8))
    }

    @Test
    fun directionJumpRestartsConfirmation() {
        val gate = GroundingRequestGate(stableFramesRequired = 3, cooldownMs = 0)
        assertFalse(gate.shouldRequest(hint(1), 1))
        assertFalse(gate.shouldRequest(hint(2), 2))
        assertFalse(gate.shouldRequest(hint(3, dirX = 0f, dirY = 1f), 3))
        assertFalse(gate.shouldRequest(hint(4, dirX = 0f, dirY = 1f), 4))
        assertTrue(gate.shouldRequest(hint(5, dirX = 0f, dirY = 1f), 5))
    }

    @Test
    fun cacheExpiresAndUsesStableHighIds() {
        val cache = GroundedCandidateCache(ttlMs = 100)
        val frame = FrameDetections(100, 100, emptyList())
        val box = BoxObservation("object", "hat", 0f, 10f, 10f, 30f, 30f)
        val request = GroundingHint(1, 100, 100, 20f, 20f, 1f, 0f, true)
        cache.replace(listOf(box), request, 1000)
        val active = cache.append(frame, request.copy(sequence = 2), 1050)
        assertEquals(1, active.boxes.size)
        assertEquals(GroundedCandidateCache.GROUNDED_TRACK_BASE, active.boxes.single().trackId)
        assertTrue(cache.append(frame, request.copy(sequence = 3), 1101).boxes.isEmpty())
    }

    @Test
    fun cacheDropsStaticBoxesWhenFingerReaims() {
        val cache = GroundedCandidateCache(ttlMs = 1000)
        val frame = FrameDetections(1080, 1440, emptyList())
        val request = hint(1)
        cache.replace(
            listOf(BoxObservation("object", "laundry basket", 0f, 10f, 10f, 200f, 200f)),
            request,
            1000,
        )
        assertEquals(1, cache.append(frame, hint(2), 1010).boxes.size)
        assertTrue(
            "A prior target's static grounding must not leak into a new ray episode",
            cache.append(frame, hint(3, dirX = 0f, dirY = -1f), 1020).boxes.isEmpty(),
        )
    }
}
