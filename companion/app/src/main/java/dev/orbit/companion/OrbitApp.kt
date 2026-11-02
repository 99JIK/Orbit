package dev.orbit.companion

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager

class OrbitApp : Application() {
    override fun onCreate() {
        super.onCreate()
        val channel = NotificationChannel(
            CHANNEL_SERVICE,
            getString(R.string.channel_service),
            NotificationManager.IMPORTANCE_LOW,
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    companion object {
        const val CHANNEL_SERVICE = "service"
    }
}
