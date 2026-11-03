package dev.orbit.companion

import android.content.Context
import android.os.Environment
import java.io.File

/** 기기 로컬 설정. 서버 주소·토큰과 수집 동작을 담는다. */
class Settings(context: Context) {
    private val prefs = context.getSharedPreferences("orbit", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_SERVER_URL, value.trim().trimEnd('/')).apply()

    var token: String
        get() = prefs.getString(KEY_TOKEN, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_TOKEN, value.trim()).apply()

    /** 서버가 토큰으로 식별해 돌려준 기기 ID. 연결 확인 전에는 비어 있다. */
    var deviceId: String
        get() = prefs.getString(KEY_DEVICE_ID, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_DEVICE_ID, value).apply()

    val isConfigured: Boolean
        get() = serverUrl.isNotEmpty() && token.isNotEmpty()

    var collecting: Boolean
        get() = prefs.getBoolean(KEY_COLLECTING, false)
        set(value) = prefs.edit().putBoolean(KEY_COLLECTING, value).apply()

    /** 녹음 파일을 Wi-Fi 등 비과금 망에서만 보낸다. */
    var wifiOnlyAudio: Boolean
        get() = prefs.getBoolean(KEY_WIFI_ONLY_AUDIO, false)
        set(value) = prefs.edit().putBoolean(KEY_WIFI_ONLY_AUDIO, value).apply()

    /**
     * 이 시각(epoch ms) 이전에 수정된 녹음 파일은 수집하지 않는다.
     * 수집을 처음 켤 때 기록해, 기존 녹음 전체가 한꺼번에 올라가는 것을 막는다.
     */
    var watchSince: Long
        get() = prefs.getLong(KEY_WATCH_SINCE, 0L)
        set(value) = prefs.edit().putLong(KEY_WATCH_SINCE, value).apply()

    var lastSentAt: Long
        get() = prefs.getLong(KEY_LAST_SENT_AT, 0L)
        set(value) = prefs.edit().putLong(KEY_LAST_SENT_AT, value).apply()

    var lastError: String
        get() = prefs.getString(KEY_LAST_ERROR, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_LAST_ERROR, value).apply()

    /**
     * 통화 녹음 폴더. 내부 저장소 기준 상대 경로 또는 절대 경로. 비우면 감시하지 않는다.
     * 기본값은 One UI 기본 통화 녹음 위치지만 기종·버전마다 달라서 사용자가 바꿀 수 있다.
     */
    var callDir: String
        get() = prefs.getString(KEY_CALL_DIR, DEFAULT_CALL_DIR) ?: DEFAULT_CALL_DIR
        set(value) = prefs.edit().putString(KEY_CALL_DIR, value.trim().trimEnd('/')).apply()

    /** 음성 녹음 폴더. 규칙은 [callDir]와 같다. */
    var voiceDir: String
        get() = prefs.getString(KEY_VOICE_DIR, DEFAULT_VOICE_DIR) ?: DEFAULT_VOICE_DIR
        set(value) = prefs.edit().putString(KEY_VOICE_DIR, value.trim().trimEnd('/')).apply()

    /** 감시 대상 폴더. */
    val watchedDirs: List<WatchedDir>
        get() = listOfNotNull(
            resolveDir(callDir)?.let { WatchedDir(it, origin = "call") },
            resolveDir(voiceDir)?.let { WatchedDir(it, origin = "voice") },
        ).distinctBy { it.dir }

    private companion object {
        const val KEY_SERVER_URL = "server_url"
        const val KEY_TOKEN = "token"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_COLLECTING = "collecting"
        const val KEY_WIFI_ONLY_AUDIO = "wifi_only_audio"
        const val KEY_WATCH_SINCE = "watch_since"
        const val KEY_LAST_SENT_AT = "last_sent_at"
        const val KEY_LAST_ERROR = "last_error"
        const val KEY_CALL_DIR = "call_dir"
        const val KEY_VOICE_DIR = "voice_dir"
        const val DEFAULT_CALL_DIR = "Recordings/Call"
        const val DEFAULT_VOICE_DIR = "Recordings/Voice Recorder"
    }
}

/** 설정값을 실제 폴더로 바꾼다. 상대 경로는 내부 저장소 루트 기준이다. */
fun resolveDir(path: String): File? {
    if (path.isBlank()) return null
    val f = File(path)
    return if (f.isAbsolute) f else File(Environment.getExternalStorageDirectory(), path)
}

/** 내부 저장소 루트 아래면 상대 경로로, 아니면 절대 경로로 보여 준다. */
fun displayPath(dir: File): String {
    val root = Environment.getExternalStorageDirectory().absolutePath
    return dir.absolutePath.removePrefix("$root/").ifEmpty { dir.absolutePath }
}

/** 감시 폴더와, 그 폴더 파일의 출처 구분(`payload.origin`). */
data class WatchedDir(val dir: File, val origin: String)
