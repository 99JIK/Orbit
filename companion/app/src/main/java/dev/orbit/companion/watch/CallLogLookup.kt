package dev.orbit.companion.watch

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.CallLog
import dev.orbit.companion.ingest.Events
import org.json.JSONObject
import kotlin.math.abs

/**
 * 통화 녹음 파일에 대응하는 통화 기록을 찾는다 (docs/companion.md 2.2).
 *
 * One UI 통화 녹음 파일의 수정 시각은 통화 종료 시각과 거의 같으므로,
 * 통화 종료 시각(`DATE + DURATION`)이 파일 수정 시각에 가장 가까운 기록을 고른다.
 */
object CallLogLookup {
    private const val MATCH_TOLERANCE_MS = 2 * 60 * 1000L
    private const val SEARCH_WINDOW_MS = 6 * 60 * 60 * 1000L

    fun find(context: Context, recordingModifiedAt: Long): JSONObject? {
        if (context.checkSelfPermission(Manifest.permission.READ_CALL_LOG) != PackageManager.PERMISSION_GRANTED) {
            return null
        }
        val projection = arrayOf(
            CallLog.Calls.NUMBER,
            CallLog.Calls.CACHED_NAME,
            CallLog.Calls.TYPE,
            CallLog.Calls.DATE,
            CallLog.Calls.DURATION,
        )
        val selection = "${CallLog.Calls.DATE} BETWEEN ? AND ?"
        val args = arrayOf(
            (recordingModifiedAt - SEARCH_WINDOW_MS).toString(),
            (recordingModifiedAt + MATCH_TOLERANCE_MS).toString(),
        )

        var best: JSONObject? = null
        var bestGap = Long.MAX_VALUE
        context.contentResolver.query(CallLog.Calls.CONTENT_URI, projection, selection, args, null)?.use { cursor ->
            while (cursor.moveToNext()) {
                val startedAt = cursor.getLong(3)
                val durationSec = cursor.getLong(4)
                val gap = abs(startedAt + durationSec * 1000 - recordingModifiedAt)
                if (gap > MATCH_TOLERANCE_MS || gap >= bestGap) continue
                bestGap = gap
                best = JSONObject().apply {
                    put("number", cursor.getString(0).orEmpty())
                    cursor.getString(1)?.takeIf { it.isNotBlank() }?.let { put("name", it) }
                    put("direction", direction(cursor.getInt(2)))
                    put("started_at", Events.isoTime(startedAt))
                    put("duration_s", durationSec)
                }
            }
        }
        return best
    }

    private fun direction(type: Int): String = when (type) {
        CallLog.Calls.INCOMING_TYPE -> "incoming"
        CallLog.Calls.OUTGOING_TYPE -> "outgoing"
        else -> "other"
    }
}
