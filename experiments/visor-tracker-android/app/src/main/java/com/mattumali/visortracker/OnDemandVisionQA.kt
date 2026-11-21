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
        .readTimeout(30, TimeUnit.SECONDS)
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

    private fun queryVlmEndpoint(bitmap: Bitmap?, question: String): String {
        if (bitmap == null) return "No frame available for vision analysis."
        val apiKey = System.getenv("GEMINI_API_KEY")
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
            put("contents", JSONArray().apply {
                put(JSONObject().apply {
                    put("parts", JSONArray().apply {
                        put(JSONObject().apply {
                            put("inline_data", JSONObject().apply {
                                put("mime_type", "image/jpeg")
                                put("data", base64Image)
                            })
                        })
                        put(JSONObject().apply {
                            put("text", question)
                        })
                    })
                })
            })
        }

        val mediaType = "application/json; charset=utf-8".toMediaType()
        val requestBody = jsonPayload.toString().toRequestBody(mediaType)
        val url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=$apiKey"
        val request = Request.Builder()
            .url(url)
            .post(requestBody)
            .build()

        httpClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                return "Gemini API returned error: HTTP ${response.code}"
            }
            val responseBody = response.body?.string() ?: return "Empty response from Gemini API"
            val jsonResp = JSONObject(responseBody)
            val candidates = jsonResp.optJSONArray("candidates")
            if (candidates != null && candidates.length() > 0) {
                val candidate = candidates.getJSONObject(0)
                val content = candidate.optJSONObject("content")
                val parts = content?.optJSONArray("parts")
                if (parts != null && parts.length() > 0) {
                    return parts.getJSONObject(0).optString("text", "No response text")
                }
            }
            return "Unable to parse response from Gemini API"
        }
    }

    companion object {
        private const val TAG = "OnDemandVisionQA"
    }
}
