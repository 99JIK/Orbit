package dev.orbit.companion.ingest

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * 홈서버 수집 API 클라이언트 (docs/ingest-api.md).
 * 네트워크 오류는 [IOException]으로 던지고, HTTP 응답은 상태 코드로 돌려준다.
 */
class IngestClient(baseUrl: String, private val token: String) {
    private val base = baseUrl.trimEnd('/')

    @Throws(IOException::class)
    fun postEvent(eventJson: String): Int = execute("/ingest", eventJson.toRequestBody(JSON))

    @Throws(IOException::class)
    fun postFile(eventJson: String, file: File, mimeType: String): Int {
        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("event", eventJson)
            .addFormDataPart("file", file.name, file.asRequestBody(mimeType.toMediaType()))
            .build()
        return execute("/ingest/file", body)
    }

    @Throws(IOException::class)
    fun heartbeat(json: JSONObject): Int = execute("/heartbeat", json.toString().toRequestBody(JSON))

    /** `GET /health`. 토큰이 유효하면 응답에 `device_id`가 들어 있다. */
    @Throws(IOException::class)
    fun health(): Pair<Int, JSONObject?> {
        val request = Request.Builder().url("$base/health").authorized().get().build()
        http.newCall(request).execute().use { response ->
            val body = response.body.string()
            val json = runCatching { JSONObject(body) }.getOrNull()
            return response.code to json
        }
    }

    private fun execute(path: String, body: RequestBody): Int {
        val request = Request.Builder().url(base + path).authorized().post(body).build()
        http.newCall(request).execute().use { return it.code }
    }

    private fun Request.Builder.authorized() = header("Authorization", "Bearer $token")

    private companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()

        val http: OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(120, TimeUnit.SECONDS)
            .build()
    }
}
