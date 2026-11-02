package dev.orbit.companion.ingest

import android.content.Context
import dev.orbit.companion.Settings
import dev.orbit.companion.data.OrbitDatabase
import dev.orbit.companion.data.OutboxItem
import dev.orbit.companion.work.UploadWorker
import org.json.JSONObject
import java.io.File

/** 수집한 입력을 이벤트로 만들어 전송 큐에 넣는다. 넣은 뒤 전송 작업을 예약한다. */
class Outbox(private val context: Context) {
    private val dao = OrbitDatabase.get(context).dao()
    private val settings = Settings(context)

    suspend fun addText(text: String, title: String?, capturedAt: Long, app: String?) {
        val payload = JSONObject().apply {
            if (!title.isNullOrBlank()) put("title", title)
            put("text", text)
        }
        val event = Events.build(
            type = "share",
            capturedAt = capturedAt,
            payload = payload,
            dedupeKey = Events.sha256(text),
            deviceId = settings.deviceId,
            app = app,
        )
        enqueue(event, file = null, mimeType = null, deleteAfterSend = false, isAudio = false)
    }

    /**
     * 파일 이벤트를 큐에 넣는다.
     *
     * @param origin `call` | `voice` | `share`
     * @param deleteAfterSend 앱 전용 저장소에 복사한 파일이면 true
     */
    suspend fun addFile(
        file: File,
        displayName: String,
        mimeType: String,
        origin: String,
        capturedAt: Long,
        durationMs: Long?,
        deleteAfterSend: Boolean,
        app: String? = null,
    ) {
        val isAudio = mimeType.startsWith("audio/")
        val payload = JSONObject().apply {
            put("title", displayName.substringBeforeLast('.'))
            put("origin", origin)
            put("file_name", displayName)
            put("mime", mimeType)
            put("size", file.length())
            if (durationMs != null) put("duration_ms", durationMs)
        }
        val event = Events.build(
            type = if (isAudio) "audio" else "share",
            capturedAt = capturedAt,
            payload = payload,
            dedupeKey = Events.sha256(file),
            deviceId = settings.deviceId,
            app = app,
        )
        enqueue(event, file, mimeType, deleteAfterSend, isAudio)
    }

    private suspend fun enqueue(
        event: JSONObject,
        file: File?,
        mimeType: String?,
        deleteAfterSend: Boolean,
        isAudio: Boolean,
    ) {
        dao.enqueue(
            OutboxItem(
                eventId = event.getString("event_id"),
                eventJson = event.toString(),
                filePath = file?.absolutePath,
                mimeType = mimeType,
                deleteFileAfterSend = deleteAfterSend,
                isAudio = isAudio,
                createdAt = System.currentTimeMillis(),
            ),
        )
        UploadWorker.schedule(context)
    }
}
