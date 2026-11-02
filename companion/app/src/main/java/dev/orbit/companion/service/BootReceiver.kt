package dev.orbit.companion.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import dev.orbit.companion.Settings
import dev.orbit.companion.work.HeartbeatWorker

/** 재부팅·앱 업데이트 후 수집을 다시 시작한다. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val settings = Settings(context)
        if (!settings.isConfigured) return
        HeartbeatWorker.schedule(context)
        if (settings.collecting) CompanionService.tryStart(context)
    }
}
