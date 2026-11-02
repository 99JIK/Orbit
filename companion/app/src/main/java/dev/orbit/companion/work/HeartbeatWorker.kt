package dev.orbit.companion.work

import android.content.Context
import android.os.Environment
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import dev.orbit.companion.BuildConfig
import dev.orbit.companion.Settings
import dev.orbit.companion.data.OrbitDatabase
import dev.orbit.companion.ingest.Events
import dev.orbit.companion.ingest.IngestClient
import dev.orbit.companion.service.CompanionService
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * 15분마다 서버에 상태를 보고한다 (스펙 3.3).
 * 수집이 켜져 있는데 서비스가 죽어 있으면 다시 띄우고, 큐가 남아 있으면 전송을 예약한다.
 */
class HeartbeatWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val settings = Settings(applicationContext)
        if (!settings.isConfigured) return Result.success()

        if (settings.collecting && !CompanionService.running) {
            CompanionService.tryStart(applicationContext)
        }

        val queueLen = OrbitDatabase.get(applicationContext).dao().count()
        if (queueLen > 0) UploadWorker.schedule(applicationContext)

        val body = JSONObject().apply {
            if (settings.deviceId.isNotEmpty()) put("device_id", settings.deviceId)
            put("at", Events.isoTime(System.currentTimeMillis()))
            put("app_version", BuildConfig.VERSION_NAME)
            put("listener_ok", listenerOk(settings))
            put("queue_len", queueLen)
        }
        try {
            IngestClient(settings.serverUrl, settings.token).heartbeat(body)
        } catch (_: IOException) {
            // 다음 주기에 다시 보낸다. 서버는 무신호 시간으로 장애를 판단한다.
        }
        return Result.success()
    }

    /** 수집을 켠 상태에서 감시가 실제로 동작하고 있는가. 수집을 끈 상태면 정상으로 본다. */
    private fun listenerOk(settings: Settings): Boolean =
        !settings.collecting ||
            (CompanionService.running && Environment.isExternalStorageManager())

    companion object {
        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<HeartbeatWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                "heartbeat",
                ExistingPeriodicWorkPolicy.UPDATE,
                request,
            )
        }
    }
}
