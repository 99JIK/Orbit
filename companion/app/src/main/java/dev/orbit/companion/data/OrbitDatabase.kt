package dev.orbit.companion.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(entities = [OutboxItem::class, SeenFile::class], version = 1)
abstract class OrbitDatabase : RoomDatabase() {
    abstract fun dao(): OrbitDao

    companion object {
        @Volatile
        private var instance: OrbitDatabase? = null

        fun get(context: Context): OrbitDatabase =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    context.applicationContext,
                    OrbitDatabase::class.java,
                    "orbit.db",
                ).build().also { instance = it }
            }
    }
}
