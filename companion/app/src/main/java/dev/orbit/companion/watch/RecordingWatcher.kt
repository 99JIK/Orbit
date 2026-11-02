package dev.orbit.companion.watch

import android.content.Context
import android.media.MediaMetadataRetriever
import android.os.Environment
import android.os.FileObserver
import android.webkit.MimeTypeMap
import dev.orbit.companion.Settings
import dev.orbit.companion.WatchedDir
import dev.orbit.companion.data.OrbitDatabase
import dev.orbit.companion.data.SeenFile
import dev.orbit.companion.ingest.Outbox
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.File

/**
 * 녹음 폴더를 감시해 새 파일을 전송 큐에 넣는다 (스펙 3.1·3.2).
 *
 * [FileObserver]는 프로세스가 살아 있는 동안의 변경만 받으므로, [rescan]으로 놓친 파일을 보충한다.
 * 아직 없는 폴더(첫 통화 녹음 전)는 감시하지 못하므로, 재검사 때 감시를 다시 건다.
 */
class RecordingWatcher(context: Context, private val scope: CoroutineScope) {
    private val settings = Settings(context)
    private val dao = OrbitDatabase.get(context).dao()
    private val outbox = Outbox(context)
    private val lock = Mutex()
    private val observers = mutableMapOf<File, FileObserver>()

    fun start() {
        if (settings.watchSince == 0L) settings.watchSince = System.currentTimeMillis()
        rescan()
    }

    fun stop() {
        observers.values.forEach { it.stopWatching() }
        observers.clear()
    }

    /** 감시를 (다시) 걸고 폴더 전체를 훑는다. */
    fun rescan() {
        if (!Environment.isExternalStorageManager()) return
        for (watched in settings.watchedDirs) {
            ensureObserver(watched)
            scope.launch {
                watched.dir.listFiles()?.sortedBy { it.lastModified() }?.forEach { consider(it, watched.origin) }
            }
        }
    }

    private fun ensureObserver(watched: WatchedDir) {
        if (!watched.dir.isDirectory || watched.dir in observers) return
        val observer = object : FileObserver(watched.dir, CLOSE_WRITE or MOVED_TO) {
            override fun onEvent(event: Int, path: String?) {
                if (path == null) return
                scope.launch { consider(File(watched.dir, path), watched.origin) }
            }
        }
        observer.startWatching()
        observers[watched.dir] = observer
    }

    private suspend fun consider(file: File, origin: String) = lock.withLock {
        if (!file.isFile || file.extension.lowercase() !in AUDIO_EXTENSIONS) return@withLock
        val modifiedAt = file.lastModified()
        if (modifiedAt < settings.watchSince) return@withLock
        // 재검사 중 쓰는 중인 파일을 만나면 건너뛴다. 쓰기가 끝나면 CLOSE_WRITE로 다시 들어온다.
        if (System.currentTimeMillis() - modifiedAt < WRITE_SETTLE_MS) return@withLock

        val seen = dao.seenFile(file.absolutePath)
        if (seen != null && seen.size == file.length() && seen.modifiedAt == modifiedAt) return@withLock

        val durationMs = durationOf(file)
        if (durationMs == null || durationMs >= MIN_DURATION_MS) {
            outbox.addFile(
                file = file,
                displayName = file.name,
                mimeType = mimeTypeOf(file),
                origin = origin,
                capturedAt = modifiedAt,
                durationMs = durationMs,
                deleteAfterSend = false,
            )
        }
        dao.markSeen(SeenFile(file.absolutePath, file.length(), modifiedAt))
    }

    private fun durationOf(file: File): Long? = runCatching {
        MediaMetadataRetriever().use { retriever ->
            retriever.setDataSource(file.absolutePath)
            retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull()
        }
    }.getOrNull()

    private fun mimeTypeOf(file: File): String =
        MimeTypeMap.getSingleton().getMimeTypeFromExtension(file.extension.lowercase()) ?: "audio/mp4"

    private companion object {
        val AUDIO_EXTENSIONS = setOf("m4a", "mp3", "ogg", "wav")
        const val MIN_DURATION_MS = 2_000L
        const val WRITE_SETTLE_MS = 3_000L
    }
}
