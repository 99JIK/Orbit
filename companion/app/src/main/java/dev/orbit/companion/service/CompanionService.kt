package dev.orbit.companion.service

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.util.Log
import androidx.core.content.ContextCompat
import dev.orbit.companion.OrbitApp
import dev.orbit.companion.R
import dev.orbit.companion.data.OrbitDatabase
import dev.orbit.companion.ui.MainActivity
import dev.orbit.companion.watch.RecordingWatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** 녹음 폴더 감시를 상시 유지하는 포그라운드 서비스 (스펙 3.1). */
class CompanionService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var watcher: RecordingWatcher

    override fun onCreate() {
        super.onCreate()
        startForeground(NOTIFICATION_ID, notification(0), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        running = true

        watcher = RecordingWatcher(this, scope)
        watcher.start()
        scope.launch {
            while (isActive) {
                delay(RESCAN_INTERVAL_MS)
                watcher.rescan()
            }
        }
        scope.launch {
            val manager = getSystemService(NotificationManager::class.java)
            OrbitDatabase.get(this@CompanionService).dao().observeCount().collect { pending ->
                manager.notify(NOTIFICATION_ID, notification(pending))
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        running = false
        watcher.stop()
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun notification(pending: Int): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val text = if (pending == 0) "녹음 폴더 감시 중" else "녹음 폴더 감시 중 · 전송 대기 ${pending}건"
        return Notification.Builder(this, OrbitApp.CHANNEL_SERVICE)
            .setSmallIcon(R.drawable.ic_orbit)
            .setContentTitle("Orbit")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val NOTIFICATION_ID = 1
        private const val RESCAN_INTERVAL_MS = 10 * 60 * 1000L

        @Volatile
        var running = false
            private set

        /** 백그라운드에서 포그라운드 서비스 시작이 막히는 경우가 있어 실패를 삼킨다. */
        fun tryStart(context: Context) {
            try {
                ContextCompat.startForegroundService(context, Intent(context, CompanionService::class.java))
            } catch (e: IllegalStateException) {
                Log.w("Orbit", "포그라운드 서비스 시작 실패", e)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, CompanionService::class.java))
        }
    }
}
