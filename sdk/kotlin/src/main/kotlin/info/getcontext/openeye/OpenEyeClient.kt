package info.getcontext.openeye

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * HTTP client for the OpenEye sidecar — Kotlin/Android edition.
 *
 * Mirrors the TypeScript SidecarClient and Swift OpenEyeClient API.
 *
 * IMPORTANT: Android apps cannot spawn the Python sidecar. Run the sidecar
 * on a server or workstation, then point this client at it over HTTPS in
 * production or HTTP on a LAN for development.
 *
 * Quick start:
 * ```kotlin
 * val client = OpenEyeClient("http://192.168.1.50:7770")
 * val sessionId = client.createSession(source = "android-app")
 * val vsid = client.createVisualSession(deviceType = "android")
 * client.logFrame(vsid, sequenceNum = 1, sceneDescription = "operator placing bolt")
 * client.logStepVerification(vsid, stepId = "s1", result = VerifyResult.PASS)
 * ```
 */
class OpenEyeClient(
    private val baseUrl: String,
    private val sidecarToken: String? = null,
    private val requestTimeoutSeconds: Long = 15,
    private val httpClient: OkHttpClient = defaultClient(requestTimeoutSeconds),
) {
    enum class VerifyResult(val value: String) {
        PASS("pass"),
        FAIL("fail"),
        UNCERTAIN("uncertain"),
    }

    class OpenEyeHttpException(val status: Int, val body: String) :
        RuntimeException("OpenEye HTTP $status: $body")

    companion object {
        private val JSON_MEDIA = "application/json".toMediaType()
        private val json = Json { ignoreUnknownKeys = true }

        private fun defaultClient(timeoutSeconds: Long) = OkHttpClient.Builder()
            .connectTimeout(timeoutSeconds, TimeUnit.SECONDS)
            .readTimeout(timeoutSeconds, TimeUnit.SECONDS)
            .writeTimeout(timeoutSeconds, TimeUnit.SECONDS)
            // SSE streams need no read timeout
            .build()
    }

    private fun urlFor(path: String): String =
        "${baseUrl.trimEnd('/')}/${path.trimStart('/')}"

    private fun buildRequest(method: String, path: String, body: Any? = null): Request {
        val builder = Request.Builder().url(urlFor(path))
        sidecarToken?.let { builder.header("Authorization", "Bearer $it") }
        when (method.uppercase()) {
            "GET" -> builder.get()
            "POST" -> {
                val payload = if (body == null) {
                    "".toRequestBody(JSON_MEDIA)
                } else {
                    json.encodeToString(JsonObject.serializer(),
                        body as JsonObject).toRequestBody(JSON_MEDIA)
                }
                builder.post(payload)
            }
            else -> throw IllegalArgumentException("Unsupported method: $method")
        }
        return builder.build()
    }

    private suspend fun executeForJson(request: Request): JsonObject =
        suspendCancellableCoroutine { cont ->
            val call = httpClient.newCall(request)
            cont.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    cont.resumeWithException(e)
                }
                override fun onResponse(call: Call, response: Response) {
                    response.use {
                        val bodyText = it.body?.string() ?: ""
                        if (!it.isSuccessful) {
                            cont.resumeWithException(
                                OpenEyeHttpException(it.code, bodyText))
                            return
                        }
                        val parsed = if (bodyText.isBlank()) JsonObject(emptyMap())
                            else json.parseToJsonElement(bodyText).jsonObject
                        cont.resume(parsed)
                    }
                }
            })
        }

    private fun jsonObjectOf(vararg pairs: Pair<String, JsonElement?>): JsonObject =
        JsonObject(pairs.mapNotNull { (k, v) -> if (v == null) null else k to v }.toMap())

    // MARK: - Health

    suspend fun health(): Boolean {
        val resp = executeForJson(buildRequest("GET", "health"))
        return (resp["ok"] as? kotlinx.serialization.json.JsonPrimitive)?.content?.toBooleanStrictOrNull() == true
    }

    // MARK: - Sessions

    suspend fun createSession(
        source: String = "android",
        userId: String? = null,
        tenantId: String? = null,
        model: String? = null,
    ): String {
        val body = jsonObjectOf(
            "source" to kotlinx.serialization.json.JsonPrimitive(source),
            "user_id" to userId?.let { kotlinx.serialization.json.JsonPrimitive(it) },
            "tenant_id" to tenantId?.let { kotlinx.serialization.json.JsonPrimitive(it) },
            "model" to model?.let { kotlinx.serialization.json.JsonPrimitive(it) },
        )
        val resp = executeForJson(buildRequest("POST", "sessions/create", body))
        return resp["session_id"]!!.jsonPrimitive.content
    }

    suspend fun endSession(sessionId: String, reason: String = "completed") {
        val body = jsonObjectOf("reason" to kotlinx.serialization.json.JsonPrimitive(reason))
        executeForJson(buildRequest("POST", "sessions/$sessionId/end", body))
    }

    // MARK: - Visual sessions

    suspend fun createVisualSession(
        deviceType: String,
        deviceId: String? = null,
        procedureId: String? = null,
        procedureName: String? = null,
        tenantId: String? = null,
        sessionId: String? = null,
    ): String {
        val body = jsonObjectOf(
            "device_type" to kotlinx.serialization.json.JsonPrimitive(deviceType),
            "device_id" to deviceId?.let { kotlinx.serialization.json.JsonPrimitive(it) },
            "procedure_id" to procedureId?.let { kotlinx.serialization.json.JsonPrimitive(it) },
            "procedure_name" to procedureName?.let { kotlinx.serialization.json.JsonPrimitive(it) },
            "tenant_id" to tenantId?.let { kotlinx.serialization.json.JsonPrimitive(it) },
            "session_id" to sessionId?.let { kotlinx.serialization.json.JsonPrimitive(it) },
        )
        val resp = executeForJson(buildRequest("POST", "visual-sessions/create", body))
        return resp["visual_session_id"]!!.jsonPrimitive.content
    }

    suspend fun endVisualSession(vsid: String, outcome: String = "completed") {
        val body = jsonObjectOf("outcome" to kotlinx.serialization.json.JsonPrimitive(outcome))
        executeForJson(buildRequest("POST", "visual-sessions/$vsid/end", body))
    }

    // MARK: - Frames

    suspend fun logFrame(
        visualSessionId: String,
        sequenceNum: Int,
        sceneDescription: String,
        stepContext: String? = null,
        confidence: Double? = null,
    ): Int {
        val body = jsonObjectOf(
            "visual_session_id" to kotlinx.serialization.json.JsonPrimitive(visualSessionId),
            "sequence_num" to kotlinx.serialization.json.JsonPrimitive(sequenceNum),
            "scene_description" to kotlinx.serialization.json.JsonPrimitive(sceneDescription),
            "step_context" to stepContext?.let { kotlinx.serialization.json.JsonPrimitive(it) },
            "confidence" to confidence?.let { kotlinx.serialization.json.JsonPrimitive(it) },
        )
        val resp = executeForJson(buildRequest("POST", "frames/log", body))
        return resp["frame_id"]!!.jsonPrimitive.content.toInt()
    }

    // MARK: - Step verification

    suspend fun logStepVerification(
        visualSessionId: String,
        stepId: String,
        result: VerifyResult,
        stepName: String? = null,
        confidence: Double? = null,
        reasoning: String? = null,
    ): Int {
        val body = jsonObjectOf(
            "visual_session_id" to kotlinx.serialization.json.JsonPrimitive(visualSessionId),
            "step_id" to kotlinx.serialization.json.JsonPrimitive(stepId),
            "result" to kotlinx.serialization.json.JsonPrimitive(result.value),
            "step_name" to stepName?.let { kotlinx.serialization.json.JsonPrimitive(it) },
            "confidence" to confidence?.let { kotlinx.serialization.json.JsonPrimitive(it) },
            "reasoning" to reasoning?.let { kotlinx.serialization.json.JsonPrimitive(it) },
        )
        val resp = executeForJson(buildRequest("POST", "steps/log", body))
        return resp["verification_id"]!!.jsonPrimitive.content.toInt()
    }

    // MARK: - SSE stream

    data class OpenEyeEvent(
        val type: String,
        val sessionId: String?,
        val timestamp: Double,
        val data: JsonObject,
    )

    /** Flow of events from /sessions/{sessionId}/events. Use "*" for all sessions. */
    fun events(sessionId: String): Flow<OpenEyeEvent> = callbackFlow {
        val request = buildRequest("GET", "sessions/$sessionId/events")
        val listener = object : EventSourceListener() {
            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                try {
                    val obj = json.parseToJsonElement(data).jsonObject
                    val evt = OpenEyeEvent(
                        type = obj["type"]?.jsonPrimitive?.contentOrNull ?: type ?: "unknown",
                        sessionId = obj["session_id"]?.jsonPrimitive?.contentOrNull,
                        timestamp = obj["ts"]?.jsonPrimitive?.contentOrNull?.toDoubleOrNull() ?: 0.0,
                        data = obj["data"]?.jsonObject ?: JsonObject(emptyMap()),
                    )
                    trySend(evt)
                } catch (_: Exception) {
                    // Drop malformed frame; SSE keeps streaming
                }
            }
            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                close(t ?: RuntimeException("SSE failed: ${response?.code}"))
            }
            override fun onClosed(eventSource: EventSource) { close() }
        }
        val source = EventSources.createFactory(httpClient).newEventSource(request, listener)
        awaitClose { source.cancel() }
    }.flowOn(Dispatchers.IO)
}
