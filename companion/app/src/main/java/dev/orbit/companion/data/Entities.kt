package dev.orbit.companion.data

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 전송 대기 이벤트. 서버가 수락(또는 중복 판정)하면 행을 지운다.
 * 남아 있는 행이 곧 전송 큐다.
 */
@Entity(tableName = "outbox")
data class OutboxItem(
    @PrimaryKey val eventId: String,
    /** 서버로 보낼 이벤트 JSON (docs/ingest-api.md 2장). */
    val eventJson: String,
    /** 함께 보낼 파일 경로. 텍스트 이벤트는 null. */
    val filePath: String?,
    val mimeType: String?,
    /** 앱 전용 저장소에 복사한 파일이면 전송 후 지운다. 녹음 원본은 지우지 않는다. */
    val deleteFileAfterSend: Boolean,
    val isAudio: Boolean,
    val createdAt: Long,
    val attempts: Int = 0,
    val lastError: String? = null,
)

/** 이미 큐에 넣은 녹음 파일. 같은 파일을 두 번 보내지 않기 위한 기록이다. */
@Entity(tableName = "seen_files")
data class SeenFile(
    @PrimaryKey val path: String,
    val size: Long,
    val modifiedAt: Long,
)
