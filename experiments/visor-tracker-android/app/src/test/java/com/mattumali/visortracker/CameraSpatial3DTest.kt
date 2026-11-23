package com.mattumali.visortracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

class CameraSpatial3DTest {

    @Test
    fun backProjectionAndProjectionRoundTrip() {
        val intrinsics = CameraSpatial3D.CameraIntrinsics.forViewport(1080, 1440, 68f)
        val u = 540f
        val v = 720f
        val z = 2.5f

        val p = intrinsics.backProject(u, v, z)
        assertEquals(0f, p.x, 1e-4f)
        assertEquals(0f, p.y, 1e-4f)
        assertEquals(z, p.z, 1e-4f)

        val projected = intrinsics.project(p)
        assertNotNull(projected)
        assertEquals(u, projected!!.first, 1e-2f)
        assertEquals(v, projected.second, 1e-2f)
    }

    @Test
    fun constructHandRay3DPointsForward() {
        val intrinsics = CameraSpatial3D.CameraIntrinsics.forViewport(1080, 1440, 68f)
        // Hand pointing forward and slightly up-left
        val points = (0 until 21).map { i -> (500f + i * 2f) to (600f - i * 5f) }
        val depths = (0 until 21).map { i -> (i * 10f) }
        val hand = BoxObservation(
            kind = "hand",
            label = "hand",
            score = 0.95f,
            left = 490f,
            top = 500f,
            right = 550f,
            bottom = 610f,
            points = points,
            depths = depths,
            trackId = 1,
        )

        val ray3D = CameraSpatial3D.constructHandRay3D(hand, intrinsics, baseDepth = 2.0f)
        assertNotNull(ray3D)
        assertTrue("Ray must point forward into the scene", ray3D!!.direction.z > 0f)
        assertEquals(8, ray3D.sourceLandmark)
        assertEquals(5, ray3D.joints3D.size)
    }

    @Test
    fun reconstructObjectSurfaceCreatesValid3DPointCloud() {
        val intrinsics = CameraSpatial3D.CameraIntrinsics.forViewport(1080, 1440, 68f)
        val polygon = listOf(
            400f to 300f,
            600f to 300f,
            600f to 500f,
            400f to 500f,
        )
        val obj = BoxObservation(
            kind = "object",
            label = "laptop",
            score = 0.92f,
            left = 400f,
            top = 300f,
            right = 600f,
            bottom = 500f,
            points = polygon,
            depths = listOf(3.0f),
            trackId = 42,
        )

        val surface = CameraSpatial3D.reconstructObjectSurface(obj, intrinsics)
        assertNotNull(surface)
        assertEquals(42, surface!!.trackId)
        assertEquals("laptop", surface.label)
        assertEquals(3.0f, surface.meanDepth, 1e-4f)
        assertTrue("Point cloud must contain points", surface.points3D.size > 10)
        assertTrue(surface.minBound.z in 2.99f..3.01f)
    }

    @Test
    fun true3DIntersectionHitsTargetAndResolvesOcclusion() {
        val intrinsics = CameraSpatial3D.CameraIntrinsics.forViewport(1080, 1440, 68f)

        // Hand pointing directly forward through optical center (0, 0, 1)
        val ray = CameraSpatial3D.SpatialRay3D(
            origin = CameraSpatial3D.Vector3(0f, 0f, 1.0f),
            direction = CameraSpatial3D.Vector3(0f, 0f, 1.0f),
            reach = 10f,
        )

        // Foreground target at z=2.5 directly on ray
        val foregroundObj = BoxObservation(
            kind = "object",
            label = "clothes hamper",
            score = 0.88f,
            left = 440f,
            top = 620f,
            right = 640f,
            bottom = 820f,
            points = listOf(440f to 620f, 640f to 620f, 640f to 820f, 440f to 820f),
            depths = listOf(2.5f),
            trackId = 2,
        )

        // Background target at z=5.0 directly on ray (behind foreground)
        val backgroundObj = BoxObservation(
            kind = "object",
            label = "hanging clothes",
            score = 0.90f,
            left = 400f,
            top = 580f,
            right = 680f,
            bottom = 860f,
            points = listOf(400f to 580f, 680f to 580f, 680f to 860f, 400f to 860f),
            depths = listOf(5.0f),
            trackId = 5,
        )

        val surfaces = listOf(
            CameraSpatial3D.reconstructObjectSurface(backgroundObj, intrinsics)!!,
            CameraSpatial3D.reconstructObjectSurface(foregroundObj, intrinsics)!!,
        )

        val hit = CameraSpatial3D.intersect3D(ray, surfaces)
        assertNotNull(hit)
        // Foreground object (z=2.5) MUST win because it is physically closer along the 3D ray
        assertEquals(2, hit!!.trackId)
        assertEquals("clothes hamper", hit.label)
        assertEquals(1.5f, hit.distanceAlongRay, 0.1f) // 2.5 - 1.0 origin = 1.5
    }

    @Test
    fun rayMissingIn3DReturnsNullNoTarget() {
        val intrinsics = CameraSpatial3D.CameraIntrinsics.forViewport(1080, 1440, 68f)

        // Hand pointing off to the far right (x positive, y zero, z forward)
        val ray = CameraSpatial3D.SpatialRay3D(
            origin = CameraSpatial3D.Vector3(0f, 0f, 1.0f),
            direction = CameraSpatial3D.Vector3(0.95f, 0f, 0.31f).normalized(),
            reach = 10f,
        )

        // Target is on the far left
        val target = BoxObservation(
            kind = "object",
            label = "laptop",
            score = 0.95f,
            left = 50f,
            top = 600f,
            right = 200f,
            bottom = 750f,
            points = listOf(50f to 600f, 200f to 600f, 200f to 750f, 50f to 750f),
            depths = listOf(3.0f),
            trackId = 10,
        )

        val surface = CameraSpatial3D.reconstructObjectSurface(target, intrinsics)!!
        val hit = CameraSpatial3D.intersect3D(ray, listOf(surface))
        assertNull("Ray pointing away from object must return null (NO_TARGET)", hit)
    }

    @Test
    fun synthetic3DSceneKnownGeometryNumericalVerification() {
        // Construct an independent synthetic 3D ground-truth scene:
        // Camera at origin (0, 0, 0), fx=fy=1000, cx=500, cy=500
        val intrinsics = CameraSpatial3D.CameraIntrinsics(
            width = 1000,
            height = 1000,
            fx = 1000f,
            fy = 1000f,
            cx = 500f,
            cy = 500f,
            calibrationStatus = CameraSpatial3D.CalibrationStatus.CALIBRATED_METRIC,
            label = "Synthetic Known 3D Ground Truth",
        )

        // Sphere/quad target located at exact 3D coordinates (X=0.5m, Y=-0.2m, Z=3.0m)
        // Project to 2D pixels:
        // u = (0.5 * 1000 / 3.0) + 500 = 666.67 px
        // v = (-0.2 * 1000 / 3.0) + 500 = 433.33 px
        val targetU = 666.67f
        val targetV = 433.33f
        val halfW = 50f
        val halfH = 50f
        val syntheticObject = BoxObservation(
            kind = "object",
            label = "synthetic_cube",
            score = 1.0f,
            left = targetU - halfW,
            top = targetV - halfH,
            right = targetU + halfW,
            bottom = targetV + halfH,
            points = listOf(
                targetU - halfW to targetV - halfH,
                targetU + halfW to targetV - halfH,
                targetU + halfW to targetV + halfH,
                targetU - halfW to targetV + halfH,
            ),
            depths = listOf(3.0f),
            trackId = 999,
        )

        // Hand origin at (0.1m, 0.0m, 1.0m) pointing directly at (0.5m, -0.2m, 3.0m)
        val pOrigin = CameraSpatial3D.Vector3(0.1f, 0.0f, 1.0f)
        val targetCenter3D = CameraSpatial3D.Vector3(0.5f, -0.2f, 3.0f)
        val expectedVector = targetCenter3D - pOrigin // (0.4, -0.2, 2.0)
        val expectedDistance = expectedVector.magnitude() // sqrt(0.16 + 0.04 + 4.0) = sqrt(4.2) ≈ 2.04939m
        val dirRay = expectedVector.normalized()

        val ray = CameraSpatial3D.SpatialRay3D(
            origin = pOrigin,
            direction = dirRay,
            reach = 10.0f,
            calibrationStatus = CameraSpatial3D.CalibrationStatus.CALIBRATED_METRIC,
        )

        val surface = CameraSpatial3D.reconstructObjectSurface(syntheticObject, intrinsics)
        assertNotNull(surface)
        assertTrue(surface!!.isMaskRestricted)
        assertEquals(3.0f, surface.centroid.z, 0.05f)

        val hit = CameraSpatial3D.intersect3D(ray, listOf(surface))
        assertNotNull(hit)
        assertEquals(999, hit!!.trackId)
        assertEquals(CameraSpatial3D.IntersectionMode.TRUE_SURFACE_INTERSECTION, hit.intersectionMode)
        assertEquals(CameraSpatial3D.CalibrationStatus.CALIBRATED_METRIC, hit.calibrationStatus)
        assertEquals(expectedDistance, hit.distanceAlongRay, 0.06f)
        assertTrue("Perpendicular distance must be within exact threshold", hit.perpendicularDistance <= 0.08f)
        assertTrue("Geometric uncertainty must be minimal", hit.geometricUncertainty < 0.03f)

        // Test behind-the-finger negative case: object at Z=0.5m when ray origin is at Z=1.0m
        val behindObject = BoxObservation(
            kind = "object",
            label = "behind_cube",
            score = 1.0f,
            left = 450f,
            top = 450f,
            right = 550f,
            bottom = 550f,
            points = listOf(450f to 450f, 550f to 450f, 550f to 550f, 450f to 550f),
            depths = listOf(0.5f), // Behind ray origin Z=1.0m!
            trackId = 111,
        )
        val behindSurface = CameraSpatial3D.reconstructObjectSurface(behindObject, intrinsics)!!
        val behindHit = CameraSpatial3D.intersect3D(ray, listOf(behindSurface))
        assertNull("Object behind ray origin (s < 0) must be rejected and return null", behindHit)
    }

    @Test
    fun perPixelDepthSurfaceReconstructionVerification() {
        val intrinsics = CameraSpatial3D.CameraIntrinsics(
            width = 1000,
            height = 1000,
            fx = 1000f,
            fy = 1000f,
            cx = 500f,
            cy = 500f,
        )

        // Tilted sloped surface: depth ranges from 2.0m at top (v=400) to 4.0m at bottom (v=600)
        val obj = BoxObservation(
            kind = "object",
            label = "sloped_ramp",
            score = 0.95f,
            left = 400f,
            top = 400f,
            right = 600f,
            bottom = 600f,
            points = listOf(400f to 400f, 600f to 400f, 600f to 600f, 400f to 600f),
            depths = listOf(3.0f),
            trackId = 55,
        )

        // Continuous per-pixel depth lookup function
        val depthLookup: (Float, Float) -> Float = { _, normY ->
            // normY = 0.4 -> 2.0m, normY = 0.6 -> 4.0m
            2.0f + (normY - 0.4f) * 10.0f
        }

        val surface = CameraSpatial3D.reconstructObjectSurface(obj, intrinsics, depthLookup)
        assertNotNull(surface)
        assertTrue("Surface must have multiple depth layers", surface!!.maxBound.z - surface.minBound.z > 1.5f)
        assertTrue("Top of surface must be near 2.0m", surface.minBound.z in 1.9f..2.2f)
        assertTrue("Bottom of surface must be near 4.0m", surface.maxBound.z in 3.8f..4.1f)
    }

    @Test
    fun twoDMaskOverlapWithout3DIntersectionFailsClosed() {
        val intrinsics = CameraSpatial3D.CameraIntrinsics(
            width = 1000,
            height = 1000,
            fx = 1000f,
            fy = 1000f,
            cx = 500f,
            cy = 500f,
        )

        // Hand tip at (0, 0, 1.0m) pointing along +Z axis directly down the center
        val originalDir = CameraSpatial3D.Vector3(0f, 0f, 1f)
        val ray = CameraSpatial3D.SpatialRay3D(
            origin = CameraSpatial3D.Vector3(0f, 0f, 1.0f),
            direction = originalDir,
            reach = 10.0f,
            tip2D = 500f to 500f,
            dir2D = 0f to 1f, // 2D projection points "down" on the screen (increasing V)
        )

        // Object placed down on the image plane (v in 700..900, u in 400..600)
        // 2D line from (500, 500) with dir2D=(0, 1) directly enters this box!
        // BUT in 3D camera space, its 3D coordinates are Y = +0.8m to +1.6m, Z = 4.0m
        // While the 3D ray along +Z passes at X=0, Y=0, so 3D perpendicular distance is > 0.8m!
        val obj = BoxObservation(
            kind = "object",
            label = "screen_intersecting_but_3d_missing",
            score = 0.95f,
            left = 400f,
            top = 700f,
            right = 600f,
            bottom = 900f,
            points = listOf(400f to 700f, 600f to 700f, 600f to 900f, 400f to 900f),
            depths = listOf(4.0f),
            trackId = 88,
        )

        val surface = CameraSpatial3D.reconstructObjectSurface(obj, intrinsics)!!
        
        // In strict mode (strictExactOnly = true), 2D mask crossing MUST NOT fabricate a 3D hit!
        val strictHit = CameraSpatial3D.intersect3D(ray, listOf(surface), strictExactOnly = true, intrinsics = intrinsics)
        assertNull("2D mask crossing must NOT fabricate an exact 3D hit when 3D ray misses surface", strictHit)

        // In non-strict mode, if any proximity hit occurs, ray direction must remain the original ray direction!
        val proxHit = CameraSpatial3D.intersect3D(ray, listOf(surface), strictExactOnly = false, intrinsics = intrinsics)
        if (proxHit != null) {
            assertEquals("Ray direction must NEVER be bent or redirected to target", originalDir.x, proxHit.rayDirection.x, 1e-5f)
            assertEquals("Ray direction must NEVER be bent or redirected to target", originalDir.y, proxHit.rayDirection.y, 1e-5f)
            assertEquals("Ray direction must NEVER be bent or redirected to target", originalDir.z, proxHit.rayDirection.z, 1e-5f)
            assertTrue("Cone proximity cannot be marked TRUE_SURFACE_INTERSECTION", proxHit.intersectionMode != CameraSpatial3D.IntersectionMode.TRUE_SURFACE_INTERSECTION)
        }
    }

    @Test
    fun changingPixelDepthAltersReconstructedGeometryAndHitOutcome() {
        val intrinsics = CameraSpatial3D.CameraIntrinsics(
            width = 1000,
            height = 1000,
            fx = 1000f,
            fy = 1000f,
            cx = 500f,
            cy = 500f,
        )

        val targetBox = BoxObservation(
            kind = "object",
            label = "dynamic_depth_target",
            score = 0.95f,
            left = 450f,
            top = 450f,
            right = 550f,
            bottom = 550f,
            points = listOf(450f to 450f, 550f to 450f, 550f to 550f, 450f to 550f),
            depths = listOf(3.0f),
            trackId = 77,
        )

        // Ray origin at (0, 0, 1.0m) pointing along +Z, reach = 4.0m (can reach up to Z=5.0m)
        val ray = CameraSpatial3D.SpatialRay3D(
            origin = CameraSpatial3D.Vector3(0f, 0f, 1.0f),
            direction = CameraSpatial3D.Vector3(0f, 0f, 1.0f),
            reach = 4.0f,
        )

        // State 1: Per-pixel depth is 3.0m (within reach: s = 2.0m <= 4.0m)
        val depthLookupNear: (Float, Float) -> Float = { _, _ -> 3.0f }
        val surfaceNear = CameraSpatial3D.reconstructObjectSurface(targetBox, intrinsics, depthLookupNear)!!
        assertEquals(3.0f, surfaceNear.centroid.z, 0.05f)
        val hitNear = CameraSpatial3D.intersect3D(ray, listOf(surfaceNear), strictExactOnly = true)
        assertNotNull("Ray must hit surface at 3.0m depth", hitNear)
        assertEquals(77, hitNear!!.trackId)
        assertEquals(2.0f, hitNear.distanceAlongRay, 0.05f)

        // State 2: Changing per-pixel depth to 8.0m moves the 3D surface beyond reach (reach = 4.0m)
        val depthLookupFar: (Float, Float) -> Float = { _, _ -> 8.0f }
        val surfaceFar = CameraSpatial3D.reconstructObjectSurface(targetBox, intrinsics, depthLookupFar)!!
        assertEquals(8.0f, surfaceFar.centroid.z, 0.05f)
        val hitFar = CameraSpatial3D.intersect3D(ray, listOf(surfaceFar), strictExactOnly = true)
        assertNull("Ray must miss surface when pixel depth changes beyond ray reach", hitFar)
    }

    private fun createPointingHand(): BoxObservation {
        val pts = MutableList(21) { 125f to 215f }
        val depths = MutableList(21) { 0f }
        pts[IndexPoint.WRIST] = 100f to 250f

        pts[1] = 105f to 225f
        pts[2] = 120f to 205f
        pts[3] = 135f to 185f
        pts[4] = 150f to 165f

        pts[5] = 130f to 210f
        pts[6] = 175f to 190f
        pts[7] = 135f to 170f
        pts[8] = 170f to 145f
        depths[5] = 0f
        depths[6] = 20f
        depths[7] = 35f
        depths[8] = 45f

        pts[9] = 125f to 215f
        pts[10] = 135f to 205f
        pts[11] = 128f to 212f
        pts[12] = 120f to 220f
        pts[13] = 120f to 220f
        pts[14] = 130f to 212f
        pts[16] = 118f to 224f
        pts[17] = 112f to 225f
        pts[18] = 122f to 218f
        pts[20] = 110f to 230f

        return BoxObservation(
            kind = "hand", label = "hand", score = 0.90f, left = 95f, top = 140f, right = 180f, bottom = 255f,
            points = pts, depths = depths, trackId = 1,
        )
    }

    @Test
    fun depthFrameMismatchFailsClosedInTargetingEngine() {
        val engine = SpatialTargetingEngine(strict3DMode = true, directionFramesRequired = 1)

        val target = BoxObservation(
            kind = "object",
            label = "target_cup",
            score = 0.95f,
            left = 190f,
            top = 70f,
            right = 260f,
            bottom = 140f,
            points = listOf(190f to 70f, 260f to 70f, 260f to 140f, 190f to 140f),
            depths = listOf(2.0f),
            trackId = 10,
        )

        val hand = createPointingHand()

        // Case 1: depthFrame is NULL in strict 3D mode -> must fail closed
        val frameNoDepth = FrameDetections(
            width = 1000,
            height = 1000,
            boxes = listOf(target, hand),
            sequence = 5L,
            capturedAtMs = 500L,
            depthFrame = null,
        )
        val outNoDepth = engine.step(frameNoDepth)
        assertNull("Null depth frame in strict 3D mode must not acquire lock", outNoDepth.lockId)
        assertEquals("depth_frame_mismatch", outNoDepth.lockProvenance)

        // Case 2: depthFrame has mismatched sequence (e.g. sequence 4L vs frame sequence 5L) -> must fail closed
        val mismatchedDepthFrame = CameraSpatial3D.DepthFrame.fromBoxes(
            width = 1000,
            height = 1000,
            boxes = listOf(target),
            sequence = 4L, // MISMATCHED!
            capturedAtMs = 400L,
        )
        val frameMismatched = FrameDetections(
            width = 1000,
            height = 1000,
            boxes = listOf(target, hand),
            sequence = 5L,
            capturedAtMs = 500L,
            depthFrame = mismatchedDepthFrame,
        )
        val outMismatched = engine.step(frameMismatched)
        assertNull("Mismatched depth frame sequence must fail closed", outMismatched.lockId)
        assertEquals("depth_frame_mismatch", outMismatched.lockProvenance)

        // Case 3: Synchronized depth frame (sequence 5L == frame sequence 5L) -> acquires target
        val synchronizedDepthFrame = CameraSpatial3D.DepthFrame.fromBoxes(
            width = 1000,
            height = 1000,
            boxes = listOf(target),
            sequence = 5L, // SYNCHRONIZED!
            capturedAtMs = 500L,
        )
        val frameSynchronized = FrameDetections(
            width = 1000,
            height = 1000,
            boxes = listOf(target, hand),
            sequence = 5L,
            capturedAtMs = 500L,
            depthFrame = synchronizedDepthFrame,
        )
        // Send enough stable pointing frames to establish lock
        var outSync = engine.step(frameSynchronized)
        for (i in 0 until 5) {
            val f = FrameDetections(
                width = 1000,
                height = 1000,
                boxes = listOf(target, hand),
                sequence = (6 + i).toLong(),
                capturedAtMs = (600 + i * 100).toLong(),
                depthFrame = synchronizedDepthFrame.copy(sequence = (6 + i).toLong()),
            )
            outSync = engine.step(f)
        }
        assertTrue("Synchronized depth frame enables 3D targeting", outSync.lockProvenance in listOf("new_3d_hit", "retained_lock_grace"))
    }
}
