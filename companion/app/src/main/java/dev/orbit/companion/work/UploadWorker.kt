package dev.orbit.companion.work

import android.content.Context
import android.net.ConnectivityManager
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import dev.orbit.companion.Settings
import dev.orbit.companion.data.OrbitDatabase
import dev.orbit.companion.data.OutboxItem
import dev.orbit.companion.ingest.IngestClient
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * 전송 큐를 비운다.
 *
 * - 2xx: 수락 또는 중복. 큐에서 지운다
 * - 401·403: 토큰 문제. 작업을 멈추고 오류를 남긴다 (재시도해도 같은 결과)
 * - 그 밖의 4xx: 해당 항목만 실패 처리. [MAX_ATTEMPTS]회 실패하면 자동 재시도 대상에서 빠진다
 * - 5xx·네트워크 오류: 지수 백오프로 작업 전체를 재시도한다
 */
class UploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val settings = Settings(applicationContext)
        if (!settings.isConfigured) return Result.success()

        val dao = OrbitDatabase.get(applicationContext).dao()
        val client = IngestClient(settings.serverUrl, settings.token)
        val metered = applicationContext.getSystemService(ConnectivityManager::class.java).isActiveNetworkMetered
        var deferredAudio = false

        for (item in dao.pending(MAX_ATTEMPTS, BATCH_LIMIT)) {
            if (item.isAudio && settings.wifiOnlyAudio && metered) {
                deferredAudio = true
                continue
            }
            val file = item.filePath?.let(::File)
            if (file != null && !file.exists()) {
                dao.remove(item.eventId)
                settings.lastError = "파일이 사라져 전송하지 못함: ${file.name}"
                continue
            }

            val code = try {
                send(client, item, file)
            } catch (e: IOException) {
                settings.lastError = "서버에 연결하지 못함: ${e.message}"
                return Result.retry()
            }

            when {
                code in 200..299 -> {
                    dao.remove(item.eventId)
                    if (item.deleteFileAfterSend) file?.delete()
                    settings.lastSentAt = System.currentTimeMillis()
                    settings.lastError = ""
                }
                code == 401 || code == 403 -> {
                    settings.lastError = "서버가 토큰을 거부함 (HTTP $code)"
                    return Result.failure()
                }
                code in 400..499 -> dao.markFailed(item.eventId, "HTTP $code")
                else -> {
                    settings.lastError = "서버 오류 (HTTP $code)"
                    return Result.retry()
                }
            }
        }

        if (deferredAudio) scheduleUnmetered(applicationContext)
        return Result.success()
    }

    private fun send(client: IngestClient, item: OutboxItem, file: File?): Int =
        if (file == null) {
            client.postEvent(item.eventJson)
        } else {
            client.postFile(item.eventJson, file, item.mimeType ?: "application/octet-stream")
        }

    companion object {
        const val MAX_ATTEMPTS = 5
        private const val BATCH_LIMIT = 500

        fun schedule(context: Context) {
            WorkManager.getInstance(context).enqueueUniqueWork(
                "upload",
                ExistingWorkPolicy.APPEND_OR_REPLACE,
                request(NetworkType.CONNECTED),
            )
        }

        /** Wi-Fi 전용 녹음이 남았을 때, 비과금 망에 붙으면 다시 돈다. */
        private fun scheduleUnmetered(context: Context) {
            WorkManager.getInstance(context).enqueueUniqueWork(
                "upload-unmetered",
                ExistingWorkPolicy.KEEP,
                request(NetworkType.UNMETERED),
            )
        }

        private fun request(network: NetworkType) =
            OneTimeWorkRequestBuilder<UploadWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(network).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
    }
}
