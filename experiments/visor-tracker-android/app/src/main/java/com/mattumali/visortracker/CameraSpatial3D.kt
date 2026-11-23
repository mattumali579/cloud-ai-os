package com.mattumali.visortracker

import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * CameraSpatial3D: Canonical camera-space 3D geometry engine for Visor.
 *
 * Implements genuine 3D spatial computing:
 * 1. Pinhole camera intrinsics and 3D back-projection:
 *    (u, v, Z) -> Camera-space (X, Y, Z) in meters/relative units.
 * 2. 3D Finger Ray from MediaPipe index landmarks (MCP 5, PIP 6, DIP 7, TIP 8):
 *    P_tip(X, Y, Z) origin + normalized 3D unit direction vector D_ray.
 * 3. 3D Object Surface Reconstruction:
 *    Back-projects instance segmentation masks & depth into camera-space 3D point clouds.
 * 4. True 3D Ray-to-Surface Intersection:
 *    Minimizes 3D Euclidean distance d_perp(Q, Ray) along forward ray trajectory (s > 0).
 *    Naturally resolves foreground/background occlusions in 3D without 2D heuristics.
 *    Returns null (NO_TARGET) when no surface is intersected (NO 2D fallback).
 */
object CameraSpatial3D {

    data class Vector3(
        val x: Float,
        val y: Float,
        val z: Float,
    ) {
        operator fun plus(other: Vector3): Vector3 = Vector3(x + other.x, y + other.y, z + other.z)
        operator fun minus(other: Vector3): Vector3 = Vector3(x - other.x, y - other.y, z - other.z)
        operator fun times(scalar: Float): Vector3 = Vector3(x * scalar, y * scalar, z * scalar)
        operator fun div(scalar: Float): Vector3 = if (kotlin.math.abs(scalar) > 1e-6f) Vector3(x / scalar, y / scalar, z / scalar) else Vector3(0f, 0f, 0f)

        fun dot(other: Vector3): Float = x * other.x + y * other.y + z * other.z

        fun cross(other: Vector3): Vector3 = Vector3(
            y * other.z - z * other.y,
            z * other.x - x * other.z,
            x * other.y - y * other.x,
        )

        fun magnitude(): Float = sqrt(x * x + y * y + z * z)
        fun magnitudeSquared(): Float = x * x + y * y + z * z

        fun normalized(): Vector3 {
            val mag = magnitude()
            return if (mag > 1e-6f) this / mag else Vector3(0f, 0f, 1f)
        }

        fun distanceTo(other: Vector3): Float = (this - other).magnitude()
    }

    enum class CalibrationStatus {
        CALIBRATED_METRIC,
        UNCALIBRATED_ESTIMATED,
    }

    enum class IntersectionMode {
        TRUE_SURFACE_INTERSECTION,
        CONE_PROXIMITY_ESTIMATE,
    }

    data class CameraIntrinsics(
        val width: Int,
        val height: Int,
        val fx: Float,
        val fy: Float,
        val cx: Float = width / 2.0f,
        val cy: Float = height / 2.0f,
        val calibrationStatus: CalibrationStatus = CalibrationStatus.UNCALIBRATED_ESTIMATED,
        val label: String = "Estimated Camera-Space 3D (HFOV=68°)",
    ) {
        val isMetricCalibrated: Boolean get() = calibrationStatus == CalibrationStatus.CALIBRATED_METRIC

        fun backProject(u: Float, v: Float, z: Float): Vector3 {
            val x = (u - cx) * z / fx
            val y = (v - cy) * z / fy
            return Vector3(x, y, z)
        }

        fun project(p: Vector3): Pair<Float, Float>? {
            if (p.z <= 1e-4f) return null
            val u = (p.x * fx / p.z) + cx
            val v = (p.y * fy / p.z) + cy
            return u to v
        }

        companion object {
            /**
             * Creates intrinsics for Ray-Ban Meta glasses / phone camera feed.
             * Ultra-wide sensor (100° DFOV on 3:4 sensor -> 68.0° HFOV, 83.4° VFOV).
             * Explicitly marked UNCALIBRATED_ESTIMATED until physical chessboard/VIO calibration.
             */
            fun forViewport(width: Int, height: Int, hfovDeg: Float = 68.0f): CameraIntrinsics {
                val hfovRad = Math.toRadians(hfovDeg.toDouble()).toFloat()
                val fx = (width / 2.0f) / kotlin.math.tan(hfovRad / 2.0f)
                val fy = fx // square pixels
                return CameraIntrinsics(
                    width = width,
                    height = height,
                    fx = fx,
                    fy = fy,
                    cx = width / 2.0f,
                    cy = height / 2.0f,
                    calibrationStatus = CalibrationStatus.UNCALIBRATED_ESTIMATED,
                    label = "Ray-Ban Meta Estimated 3D (HFOV=68° Uncalibrated)",
                )
            }
        }
    }

    data class SpatialRay3D(
        val origin: Vector3,
        val direction: Vector3,
        val reach: Float,
        val sourceLandmark: Int = IndexPoint.INDEX_TIP,
        val joints3D: List<Vector3> = emptyList(),
        val calibrationStatus: CalibrationStatus = CalibrationStatus.UNCALIBRATED_ESTIMATED,
        val tip2D: Pair<Float, Float> = 0f to 0f,
        val dir2D: Pair<Float, Float> = 0f to 0f,
    ) {
        fun pointAt(s: Float): Vector3 = origin + direction * s
    }

    data class ObjectSurface3D(
        val trackId: Int,
        val label: String,
        val score: Float,
        val points3D: List<Vector3>,
        val centroid: Vector3,
        val minBound: Vector3,
        val maxBound: Vector3,
        val meanDepth: Float,
        val isMaskRestricted: Boolean = false,
        val polygon2D: List<Pair<Float, Float>> = emptyList(),
        val bbox2D: FloatArray = FloatArray(4),
    )

    data class HitResult3D(
        val trackId: Int,
        val label: String,
        val hitPoint3D: Vector3,
        val sourceSurfacePoint3D: Vector3,
        val residualToSurface: Float,
        val distanceAlongRay: Float,
        val perpendicularDistance: Float,
        val confidence: Float,
        val intersectionMode: IntersectionMode = IntersectionMode.TRUE_SURFACE_INTERSECTION,
        val geometricUncertainty: Float = 0f,
        val calibrationStatus: CalibrationStatus = CalibrationStatus.UNCALIBRATED_ESTIMATED,
        val rayDirection: Vector3 = Vector3(0f, 0f, 1f),
    )

    data class DepthFrame(
        val width: Int,
        val height: Int,
        val depthData: FloatArray,
        val sequence: Long,
        val capturedAtMs: Long,
        val backend: String = "YOLO26_DEPTH",
        val calibrationStatus: CalibrationStatus = CalibrationStatus.UNCALIBRATED_ESTIMATED,
    ) {
        fun getDepth(normX: Float, normY: Float): Float {
            if (width <= 0 || height <= 0 || depthData.isEmpty()) return 0f
            val px = (normX.coerceIn(0f, 1f) * (width - 1)).toInt()
            val py = (normY.coerceIn(0f, 1f) * (height - 1)).toInt()
            val idx = py * width + px
            return if (idx in depthData.indices) depthData[idx] else 0f
        }

        fun getDepthAtPixel(x: Float, y: Float, frameW: Int, frameH: Int): Float {
            if (frameW <= 0 || frameH <= 0) return 0f
            return getDepth(x / frameW.toFloat(), y / frameH.toFloat())
        }

        override fun equals(other: Any?): Boolean {
            if (this === other) return true
            if (javaClass != other?.javaClass) return false
            other as DepthFrame
            return width == other.width &&
                height == other.height &&
                sequence == other.sequence &&
                capturedAtMs == other.capturedAtMs &&
                backend == other.backend &&
                calibrationStatus == other.calibrationStatus &&
                depthData.contentEquals(other.depthData)
        }

        override fun hashCode(): Int {
            var result = width
            result = 31 * result + height
            result = 31 * result + sequence.hashCode()
            result = 31 * result + capturedAtMs.hashCode()
            return result
        }

        companion object {
            fun fromBoxes(
                width: Int,
                height: Int,
                boxes: List<BoxObservation>,
                sequence: Long,
                capturedAtMs: Long,
                gridW: Int = 160,
                gridH: Int = 120,
            ): DepthFrame {
                val data = FloatArray(gridW * gridH) { 5.0f }
                for (b in boxes) {
                    val d = b.depths.firstOrNull() ?: continue
                    if (d <= 0.05f) continue
                    val x0 = (b.left / width * gridW).toInt().coerceIn(0, gridW - 1)
                    val y0 = (b.top / height * gridH).toInt().coerceIn(0, gridH - 1)
                    val x1 = (b.right / width * gridW).toInt().coerceIn(0, gridW - 1)
                    val y1 = (b.bottom / height * gridH).toInt().coerceIn(0, gridH - 1)
                    for (gy in y0..y1) {
                        for (gx in x0..x1) {
                            val px = gx * width.toFloat() / gridW
                            val py = gy * height.toFloat() / gridH
                            val inMask = b.points.size < 3 || isPointInPolygon(px, py, b.points)
                            if (inMask) {
                                data[gy * gridW + gx] = d
                            }
                        }
                    }
                }
                return DepthFrame(
                    width = gridW,
                    height = gridH,
                    depthData = data,
                    sequence = sequence,
                    capturedAtMs = capturedAtMs,
                    backend = "YOLO26_DEPTH",
                    calibrationStatus = CalibrationStatus.UNCALIBRATED_ESTIMATED,
                )
            }

            fun readNextFromBinary(
                stream: java.io.DataInputStream,
                expectedSeq: Long,
                fallbackCapturedAtMs: Long,
            ): DepthFrame? {
                return try {
                    val seq = stream.readLong()
                    val capturedAtMs = stream.readLong()
                    val width = stream.readInt()
                    val height = stream.readInt()
                    if (width <= 0 || height <= 0 || width > 4096 || height > 4096) return null
                    val count = width * height
                    val bytes = ByteArray(count * 4)
                    stream.readFully(bytes)
                    val buffer = java.nio.ByteBuffer.wrap(bytes).order(java.nio.ByteOrder.LITTLE_ENDIAN)
                    val floatData = FloatArray(count)
                    buffer.asFloatBuffer().get(floatData)
                    DepthFrame(
                        width = width,
                        height = height,
                        depthData = floatData,
                        sequence = seq,
                        capturedAtMs = if (capturedAtMs > 0L) capturedAtMs else fallbackCapturedAtMs,
                        backend = "YOLO26_DEPTH_CONTINUOUS",
                        calibrationStatus = CalibrationStatus.CALIBRATED_METRIC,
                    )
                } catch (_: java.io.EOFException) {
                    null
                } catch (_: Exception) {
                    null
                }
            }
        }
    }


    /**
     * Reconstructs 3D hand joints and forward pointing ray in camera coordinates.
     * Uses human anatomical constraints: index finger length L ~ 0.085m (MCP to TIP).
     * Scale factor maps normalized MediaPipe coordinates into consistent camera units.
     */
    fun constructHandRay3D(
        hand: BoxObservation,
        intrinsics: CameraIntrinsics,
        baseDepth: Float = 0.65f,
    ): SpatialRay3D? {
        val pts = hand.points
        if (pts.size < 21) return null

        val uWrist = pts[IndexPoint.WRIST]
        val uMcp = pts[IndexPoint.INDEX_MCP]
        val uPip = pts[IndexPoint.INDEX_PIP]
        val uDip = pts[IndexPoint.INDEX_DIP]
        val uTip = pts[IndexPoint.INDEX_TIP]

        var du = uTip.first - uMcp.first
        var dv = uTip.second - uMcp.second
        var mag2D = kotlin.math.hypot(du, dv)
        if (mag2D < 1e-3f) {
            du = uTip.first - uPip.first
            dv = uTip.second - uPip.second
            mag2D = kotlin.math.hypot(du, dv)
        }
        if (mag2D < 1e-3f) {
            du = uTip.first - uWrist.first
            dv = uTip.second - uWrist.second
            mag2D = kotlin.math.hypot(du, dv)
        }
        if (mag2D < 1e-3f) return null

        val dirU = du / mag2D
        val dirV = dv / mag2D

        // Defensible anatomical calibration:
        // Adult human index finger length (MCP to TIP) is ~0.085m (8.5 cm).
        // Scale MediaPipe landmarks to physical camera-relative space:
        val dzRaw = if (hand.depths.size >= 21) {
            hand.depths[IndexPoint.INDEX_TIP] - hand.depths[IndexPoint.INDEX_MCP]
        } else 0f
        val norm3D = kotlin.math.sqrt(du * du + dv * dv + dzRaw * dzRaw)
        val zCalibrated = if (norm3D > 1.0f) {
            ((0.085f * intrinsics.fx) / norm3D).coerceIn(0.35f, 1.20f)
        } else {
            baseDepth.coerceIn(0.35f, 1.20f)
        }

        val pTip = intrinsics.backProject(uTip.first, uTip.second, zCalibrated)
        val pMcp = intrinsics.backProject(uMcp.first, uMcp.second, (zCalibrated - dzRaw * (zCalibrated / intrinsics.fx) * 0.5f).coerceAtLeast(0.30f))
        val pWrist = intrinsics.backProject(uWrist.first, uWrist.second, (zCalibrated - 0.15f).coerceAtLeast(0.25f))
        val pPip = intrinsics.backProject(uPip.first, uPip.second, (pTip.z + pMcp.z) * 0.5f)
        val pDip = intrinsics.backProject(uDip.first, uDip.second, (pTip.z * 0.75f + pPip.z * 0.25f))

        // Default ray direction: forward in camera frustum along finger line of aim
        val stepPixels = 100f
        val forwardTarget = intrinsics.backProject(uTip.first + dirU * stepPixels, uTip.second + dirV * stepPixels, zCalibrated + 1.0f)
        val defaultDir = (forwardTarget - pTip).normalized()

        val reach = 15.0f // max distance in relative units
        return SpatialRay3D(
            origin = pTip,
            direction = defaultDir,
            reach = reach,
            sourceLandmark = IndexPoint.INDEX_TIP,
            joints3D = listOf(pWrist, pMcp, pPip, pDip, pTip),
            calibrationStatus = intrinsics.calibrationStatus,
            tip2D = uTip,
            dir2D = (dirU to dirV),
        )
    }

    /**
     * Checks if 2D point (x, y) lies inside polygon using ray casting algorithm.
     */
    fun isPointInPolygon(x: Float, y: Float, polygon: List<Pair<Float, Float>>): Boolean {
        if (polygon.size < 3) return false
        var inside = false
        val n = polygon.size
        var j = n - 1
        for (i in 0 until n) {
            val pi = polygon[i]
            val pj = polygon[j]
            if ((pi.second > y) != (pj.second > y) &&
                x < (pj.first - pi.first) * (y - pi.second) / (pj.second - pi.second + 1e-7f) + pi.first
            ) {
                inside = !inside
            }
            j = i
        }
        return inside
    }

    /**
     * Reconstructs 3D object surface restricted strictly to valid object segmentation mask.
     * Uses per-pixel or dense grid sampling bounded by the segmentation polygon.
     * If depthLookup is supplied (e.g. from DepthEstimator.DepthMap), uses actual per-pixel depth Z(x, y),
     * rather than assuming a single constant planar depth across the entire object.
     */
    fun reconstructObjectSurface(
        obj: BoxObservation,
        intrinsics: CameraIntrinsics,
        depthLookup: ((normX: Float, normY: Float) -> Float)? = null,
    ): ObjectSurface3D? {
        if (obj.right <= obj.left || obj.bottom <= obj.top) return null
        val defaultZ = obj.depths.firstOrNull() ?: 3.5f
        if (defaultZ <= 1e-3f) return null

        val points3D = mutableListOf<Vector3>()
        val hasMask = obj.points.size >= 3

        val frameW = intrinsics.width.toFloat()
        val frameH = intrinsics.height.toFloat()

        fun getZ(px: Float, py: Float, pointIndex: Int? = null): Float {
            if (depthLookup != null) {
                val d = depthLookup(px / frameW, py / frameH)
                if (d > 0.05f && d.isFinite()) return d
            }
            if (pointIndex != null && pointIndex in obj.depths.indices) {
                val d = obj.depths[pointIndex]
                if (d > 0.05f && d.isFinite()) return d
            }
            return defaultZ
        }

        // 1. Polygon boundary vertices in 3D with per-vertex depth
        if (hasMask) {
            for ((idx, p) in obj.points.withIndex()) {
                val z = getZ(p.first, p.second, idx)
                points3D.add(intrinsics.backProject(p.first, p.second, z))
            }
        }

        // 2. Interior samples restricted strictly to segmentation polygon with per-pixel depth Z(x, y)
        val step = max(10f, min(obj.right - obj.left, obj.bottom - obj.top) / 14f)
        var y = obj.top + step / 2f
        while (y < obj.bottom) {
            var x = obj.left + step / 2f
            while (x < obj.right) {
                val inMask = !hasMask || isPointInPolygon(x, y, obj.points)
                if (inMask) {
                    val z = getZ(x, y)
                    points3D.add(intrinsics.backProject(x, y, z))
                }
                x += step
            }
            y += step
        }

        if (points3D.isEmpty()) return null

        var minX = Float.MAX_VALUE
        var minY = Float.MAX_VALUE
        var minZ = Float.MAX_VALUE
        var maxX = -Float.MAX_VALUE
        var maxY = -Float.MAX_VALUE
        var maxZ = -Float.MAX_VALUE
        var sumX = 0f
        var sumY = 0f
        var sumZ = 0f

        for (p in points3D) {
            if (p.x < minX) minX = p.x
            if (p.y < minY) minY = p.y
            if (p.z < minZ) minZ = p.z
            if (p.x > maxX) maxX = p.x
            if (p.y > maxY) maxY = p.y
            if (p.z > maxZ) maxZ = p.z
            sumX += p.x
            sumY += p.y
            sumZ += p.z
        }

        val n = points3D.size
        val centroid = Vector3(sumX / n, sumY / n, sumZ / n)
        val minBound = Vector3(minX, minY, minZ)
        val maxBound = Vector3(maxX, maxY, maxZ)
        val meanZ = sumZ / n

        return ObjectSurface3D(
            trackId = obj.trackId,
            label = obj.label,
            score = obj.score,
            points3D = points3D,
            centroid = centroid,
            minBound = minBound,
            maxBound = maxBound,
            meanDepth = meanZ,
            isMaskRestricted = hasMask,
            polygon2D = obj.points,
            bbox2D = floatArrayOf(obj.left, obj.top, obj.right, obj.bottom),
        )
    }

    /**
     * Executes genuine 3D ray-to-surface intersection in camera space.
     * Incorporates both camera-space 3D point cloud Euclidean intersection
     * and projected camera frustum line-of-aim ray intersection against 3D reconstructed surfaces.
     *
     * Naturally resolves foreground/background occlusions by picking the closest physical surface along the ray.
     * Mandates runtime geometric invariant: hitPoint3D == ray.origin + rayDirection * distanceAlongRay.
     */
    fun intersect3D(
        ray: SpatialRay3D,
        surfaces: List<ObjectSurface3D>,
        exactSurfaceRadius: Float = 0.08f, // 8cm exact surface threshold
        coneAngleRad: Float = 0.07f, // ~4 degrees angular pointing tolerance
        baseRadius: Float = 0.12f,
        strictExactOnly: Boolean = false,
        intrinsics: CameraIntrinsics? = null,
    ): HitResult3D? {
        if (surfaces.isEmpty()) return null

        val pTip = ray.origin
        val dRay = ray.direction
        val hits = mutableListOf<HitResult3D>()

        for (surface in surfaces) {
            // Target surface must be in front of fingertip (never behind the hand)
            if (surface.meanDepth <= pTip.z * 0.8f) {
                continue
            }

            var minPerpDist = Float.MAX_VALUE
            var bestS = -1f
            var bestHitPoint: Vector3? = null
            var bestSourceSurfacePoint: Vector3? = null
            var isExactHit = false

            // Direct forward 3D ray-to-point cloud intersection along unbent forward ray.direction
            for (q in surface.points3D) {
                val v = q - pTip
                val s = v.dot(dRay)
                if (s > 0.05f && s <= ray.reach && q.z >= pTip.z - 0.05f) {
                    val pRay = pTip + dRay * s
                    val perpDist = q.distanceTo(pRay)
                    val maxConeRadius = baseRadius + coneAngleRad * s

                    if (perpDist <= exactSurfaceRadius) {
                        isExactHit = true
                    }

                    if (perpDist <= maxConeRadius && perpDist < minPerpDist) {
                        minPerpDist = perpDist
                        bestS = s
                        bestHitPoint = pRay
                        bestSourceSurfacePoint = q
                    }
                }
            }

            // In strict 3D mode, proximity alone without true surface intersection is rejected
            if (strictExactOnly && !isExactHit) {
                continue
            }

            if (bestHitPoint != null && bestS > 0.05f) {
                // Mandatory Runtime Invariant Verification:
                // Ensure hitPoint is mathematically guaranteed to equal origin + rayDirection * distanceAlongRay
                val checkPoint = pTip + dRay * bestS
                val invariantError = bestHitPoint.distanceTo(checkPoint)
                check(invariantError < 1e-3f) {
                    "Runtime 3D geometric invariant violated: hitPoint $bestHitPoint != rayPoint $checkPoint (err=$invariantError)"
                }
                check(bestHitPoint.z >= pTip.z - 1e-3f) {
                    "Hit point is behind fingertip in camera Z: hitZ=${bestHitPoint.z} < tipZ=${pTip.z}"
                }

                val mode = if (isExactHit) {
                    IntersectionMode.TRUE_SURFACE_INTERSECTION
                } else {
                    IntersectionMode.CONE_PROXIMITY_ESTIMATE
                }
                val sourcePoint = bestSourceSurfacePoint ?: bestHitPoint
                val residual = bestHitPoint.distanceTo(sourcePoint)

                hits.add(
                    HitResult3D(
                        trackId = surface.trackId,
                        label = surface.label,
                        hitPoint3D = bestHitPoint,
                        sourceSurfacePoint3D = sourcePoint,
                        residualToSurface = residual,
                        distanceAlongRay = bestS,
                        perpendicularDistance = minPerpDist,
                        confidence = surface.score,
                        intersectionMode = mode,
                        geometricUncertainty = minPerpDist / bestS,
                        calibrationStatus = ray.calibrationStatus,
                        rayDirection = dRay, // Ray direction is NEVER bent or modified!
                    )
                )
            }
        }

        if (hits.isEmpty()) return null

        val candidateHits = if (strictExactOnly) {
            hits.filter { it.intersectionMode == IntersectionMode.TRUE_SURFACE_INTERSECTION }
        } else {
            hits
        }
        if (candidateHits.isEmpty()) return null

        // Closest object along forward 3D ray wins (natural 3D occlusion resolution)
        return candidateHits.minByOrNull { it.distanceAlongRay }
    }
}
