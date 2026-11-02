package dev.orbit.companion.ingest

import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.UUID

/** 이벤트 JSON 생성 규칙 (docs/ingest-api.md 2장). */
object Events {
    const val SOURCE = "phone"

    fun build(
        type: String,
        capturedAt: Long,
        payload: JSONObject,
        dedupeKey: String,
        deviceId: String,
        app: String? = null,
    ): JSONObject = JSONObject().apply {
        put("event_id", UUID.randomUUID().toString())
        put("source", SOURCE)
        if (!app.isNullOrEmpty()) put("app", app)
        put("type", type)
        put("captured_at", isoTime(capturedAt))
        put("payload", payload)
        put("dedupe_key", dedupeKey)
        if (deviceId.isNotEmpty()) put("device_id", deviceId)
    }

    /** epoch ms → 기기 시간대 오프셋이 붙은 ISO 8601 (초 단위). */
    fun isoTime(epochMs: Long): String =
        Instant.ofEpochMilli(epochMs)
            .truncatedTo(ChronoUnit.SECONDS)
            .atZone(ZoneId.systemDefault())
            .format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)

    fun sha256(text: String): String = hex(MessageDigest.getInstance("SHA-256").digest(text.toByteArray()))

    fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return hex(digest.digest())
    }

    private fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it) }
}
