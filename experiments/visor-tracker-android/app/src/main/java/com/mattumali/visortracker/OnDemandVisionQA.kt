package com.mattumali.visortracker

import android.content.Context
import android.graphics.Bitmap
import android.util.Base64
import android.util.Log
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit

/**
 * On-demand OCR and Visual Question-Answering (Issue #40 / Stage 1).
 *
 * Guarantees:
 * - Runs ON DEMAND only (never runs continuously in the 30 FPS video loop).
 * - Guaranteed non-blocking: executed on separate IO background coroutine,
 *   never interfering with the primary spatial targeting and overlay render loop.
 * - Explicit consent gating: cloud VLM queries (Gemini/cloud) require affirmative user consent.
 *   Throws SecurityException or returns explicit error if attempted without user authorization.
 */
class OnDemandVisionQA(
    private val context: Context? = null,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    data class OcrResult(
        val text: String,
        val lineCount: Int,
        val latencyMs: Long,
        val error: String? = null,
    )

    data class VisualQAResult(
        val question: String,
        val answer: String,
        val latencyMs: Long,
        val cloudConsentGranted: Boolean,
        val error: String? = null,
    )

    data class GroundedObject(
        val label: String,
        val ymin: Float,
        val xmin: Float,
        val ymax: Float,
        val xmax: Float,
    ) {
        fun toObservation(width: Int, height: Int): BoxObservation = BoxObservation(
            kind = "object",
            label = label,
            // Gemini detection boxes do not provide calibrated confidence.
            score = 0f,
            left = xmin * width,
            top = ymin * height,
            right = xmax * width,
            bottom = ymax * height,
            grounded = true,
        )
    }

    data class GroundedVisionResult(
        val objects: List<GroundedObject>,
        val latencyMs: Long,
        val cloudConsentGranted: Boolean,
        val error: String? = null,
    )

    /**
     * Executes on-demand local text recognition on the given frame bitmap using Google ML Kit.
     * Non-blocking callback returns OcrResult.
     */
    fun performOcr(
        bitmap: Bitmap?,
        onResult: (OcrResult) -> Unit,
    ) {
        val startedAt = System.currentTimeMillis()
        scope.launch {
            try {
                if (bitmap == null) {
                    withContext(Dispatchers.Main) {
                        onResult(OcrResult("", 0, 0, "Null bitmap"))
                    }
                    return@launch
                }
                // Local on-device OCR logic using Google ML Kit
                val text = extractLocalText(bitmap)
                val latency = System.currentTimeMillis() - startedAt
                val result = OcrResult(
                    text = text,
                    lineCount = if (text.isBlank()) 0 else text.lines().size,
                    latencyMs = latency,
                )
                withContext(Dispatchers.Main) {
                    onResult(result)
                }
            } catch (e: Exception) {
                val latency = System.currentTimeMillis() - startedAt
                val result = OcrResult(
                    text = "",
                    lineCount = 0,
                    latencyMs = latency,
                    error = e.message ?: "OCR extraction failure",
                )
                withContext(Dispatchers.Main) {
                    onResult(result)
                }
            }
        }
    }

    /**
     * Executes on-demand visual Q&A with Gemini Vision.
     * REQUIRES affirmative user consent for cloud queries.
     */
    fun askSceneQuestion(
        bitmap: Bitmap?,
        question: String,
        cloudConsentGranted: Boolean,
        onResult: (VisualQAResult) -> Unit,
    ) {
        val startedAt = System.currentTimeMillis()
        if (!cloudConsentGranted) {
            val result = VisualQAResult(
                question = question,
                answer = "",
                latencyMs = 0,
                cloudConsentGranted = false,
                error = "Explicit user authorization required for cloud VLM scene analysis",
            )
            onResult(result)
            return
        }

        scope.launch {
            try {
                // Non-blocking query to Gemini Vision endpoint
                val answer = queryVlmEndpoint(bitmap, question)
                val latency = System.currentTimeMillis() - startedAt
                val result = VisualQAResult(
                    question = question,
                    answer = answer,
                    latencyMs = latency,
                    cloudConsentGranted = true,
                )
                withContext(Dispatchers.Main) {
                    onResult(result)
                }
            } catch (e: Exception) {
                val latency = System.currentTimeMillis() - startedAt
                val result = VisualQAResult(
                    question = question,
                    answer = "",
                    latencyMs = latency,
                    cloudConsentGranted = true,
                    error = e.message ?: "VLM query failure",
                )
                withContext(Dispatchers.Main) {
                    onResult(result)
                }
            }
        }
    }

    /**
     * Uses the existing Gemini capability only after affirmative cloud consent.
     * Returned boxes must be model-grounded normalized coordinates; malformed,
     * oversized, or text-only guesses are rejected.
     */
    fun locatePointedObjects(
        bitmap: Bitmap?,
        hint: GroundingHint,
        cloudConsentGranted: Boolean,
        onResult: (GroundedVisionResult) -> Unit,
    ) {
        val startedAt = System.currentTimeMillis()
        if (!cloudConsentGranted) {
            onResult(GroundedVisionResult(
                objects = emptyList(),
                latencyMs = 0,
                cloudConsentGranted = false,
                error = "Explicit user authorization required for cloud VLM grounded localization",
            ))
            return
        }
        scope.launch {
            val result = try {
                val text = queryVlmEndpoint(bitmap, groundingPrompt(hint), jsonResponse = true)
                GroundedVisionResult(
                    objects = parseGroundedObjects(text),
                    latencyMs = System.currentTimeMillis() - startedAt,
                    cloudConsentGranted = true,
                )
            } catch (error: Exception) {
                GroundedVisionResult(
                    objects = emptyList(),
                    latencyMs = System.currentTimeMillis() - startedAt,
                    cloudConsentGranted = true,
                    error = error.message ?: "Grounded VLM localization failure",
                )
            }
            withContext(Dispatchers.Main) { onResult(result) }
        }
    }

    private fun extractLocalText(bitmap: Bitmap?): String {
        if (bitmap == null) return ""
        return try {
            val image = InputImage.fromBitmap(bitmap, 0)
            val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
            val visionText = Tasks.await(recognizer.process(image), 5000, TimeUnit.MILLISECONDS)
            recognizer.close()
            visionText.text
        } catch (e: Exception) {
            Log.e(TAG, "ML Kit OCR failed: ${e.message}", e)
            ""
        }
    }

    private fun queryVlmEndpoint(bitmap: Bitmap?, question: String, jsonResponse: Boolean = false): String {
        if (bitmap == null) return "No frame available for vision analysis."
        val apiKey = BuildConfig.VISOR_GEMINI_API_KEY.takeIf { it.isNotBlank() }
            ?: System.getenv("GEMINI_API_KEY")
            ?: System.getProperty("GEMINI_API_KEY")
            ?: ""
        if (apiKey.isBlank()) {
            return "Gemini API key not configured. Set GEMINI_API_KEY to enable cloud vision analysis."
        }

        val outputStream = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 85, outputStream)
        val jpegBytes = outputStream.toByteArray()
        val base64Image = Base64.encodeToString(jpegBytes, Base64.NO_WRAP)

        val jsonPayload = JSONObject().apply {
            // 3.8 can return capacity-only failures for interactive camera
            // requests. The existing 3.1 Flash Lite endpoint produced the
            // verified grounded boxes used by replay and is the stable default.
            put("model", "gemini-3.1-flash-lite")
            put("store", false)
            put("stream", true)
            put("input", JSONArray().apply {
                put(JSONObject().apply {
                    put("type", "text")
                    put("text", question)
                })
                put(JSONObject().apply {
                    put("type", "image")
                    put("data", base64Image)
                    put("mime_type", "image/jpeg")
                })
            })
            if (jsonResponse) {
                put("response_format", JSONObject().apply {
                    put("type", "text")
                    put("mime_type", "application/json")
                    put("schema", groundedResponseSchema())
                })
            }
        }

        val mediaType = "application/json; charset=utf-8".toMediaType()
        val requestBody = jsonPayload.toString().toRequestBody(mediaType)
        val url = "https://generativelanguage.googleapis.com/v1beta/interactions?alt=sse"
        val request = Request.Builder()
            .url(url)
            .header("x-goog-api-key", apiKey)
            .header("Api-Revision", "2026-05-20")
            .post(requestBody)
            .build()

        httpClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                return "Gemini API returned error: HTTP ${response.code}"
            }
            val source = response.body?.source() ?: return "Empty response from Gemini API"
            val text = StringBuilder()
            while (!source.exhausted()) {
                val line = source.readUtf8Line() ?: break
                if (!line.startsWith("data:")) continue
                val payload = line.removePrefix("data:").trim()
                if (payload == "[DONE]") continue
                val event = JSONObject(payload)
                if (event.optString("event_type") != "step.delta") continue
                val delta = event.optJSONObject("delta") ?: continue
                if (delta.optString("type") == "text") {
                    text.append(delta.optString("text"))
                }
            }
            if (text.isNotEmpty()) return text.toString()
            throw IllegalStateException("Gemini stream completed without model text")
        }
    }

    private fun groundedResponseSchema(): JSONObject = JSONObject().apply {
        put("type", "object")
        put("properties", JSONObject().apply {
            put("objects", JSONObject().apply {
                put("type", "array")
                put("items", JSONObject().apply {
                    put("type", "object")
                    put("properties", JSONObject().apply {
                        put("label", JSONObject().put("type", "string"))
                        put("box_2d", JSONObject().apply {
                            put("type", "array")
                            put("items", JSONObject().put("type", "integer"))
                            put("minItems", 4)
                            put("maxItems", 4)
                        })
                    })
                    put("required", JSONArray().put("label").put("box_2d"))
                })
            })
        })
        put("required", JSONArray().put("objects"))
    }

    internal fun groundingPrompt(hint: GroundingHint): String {
        val tipX = (hint.tipX / hint.frameWidth * 1000f).coerceIn(0f, 1000f).toInt()
        val tipY = (hint.tipY / hint.frameHeight * 1000f).coerceIn(0f, 1000f).toInt()
        val endX = ((hint.tipX + hint.dirX * hint.frameWidth) / hint.frameWidth * 1000f)
            .coerceIn(0f, 1000f).toInt()
        val endY = ((hint.tipY + hint.dirY * hint.frameHeight) / hint.frameHeight * 1000f)
            .coerceIn(0f, 1000f).toInt()
        return """
            Locate tangible household objects intersected by the directed image ray from
            (x=$tipX,y=$tipY) toward (x=$endX,y=$endY). Return only objects visibly grounded
            in this image, ordered nearest along the ray. Do not infer a box from text or from
            the requested label. Return JSON only in this exact shape:
            {"objects":[{"label":"concise visible name","box_2d":[ymin,xmin,ymax,xmax]}]}
            Coordinates are integers normalized 0..1000. Return {"objects":[]} when uncertain.
        """.trimIndent()
    }

    internal fun parseGroundedObjects(raw: String): List<GroundedObject> {
        val cleaned = raw.trim().removePrefix("```json").removePrefix("```").removeSuffix("```").trim()
        val root = JSONObject(cleaned)
        val array = root.optJSONArray("objects") ?: return emptyList()
        val parsed = mutableListOf<GroundedObject>()
        for (index in 0 until array.length()) {
            val item = array.optJSONObject(index) ?: continue
            val label = item.optString("label").trim().lowercase()
            val box = item.optJSONArray("box_2d") ?: continue
            if (label.isBlank() || box.length() != 4) continue
            val values = (0 until 4).map { box.optDouble(it, Double.NaN) }
            if (values.any { !it.isFinite() || it !in 0.0..1000.0 }) continue
            val ymin = (values[0] / 1000.0).toFloat()
            val xmin = (values[1] / 1000.0).toFloat()
            val ymax = (values[2] / 1000.0).toFloat()
            val xmax = (values[3] / 1000.0).toFloat()
            if (ymax <= ymin || xmax <= xmin) continue
            if ((ymax - ymin) * (xmax - xmin) > 0.14f) continue
            parsed += GroundedObject(label, ymin, xmin, ymax, xmax)
        }
        return parsed.take(8)
    }

    companion object {
        private const val TAG = "OnDemandVisionQA"
    }
}
