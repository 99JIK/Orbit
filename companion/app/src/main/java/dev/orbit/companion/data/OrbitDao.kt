package dev.orbit.companion.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface OrbitDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun enqueue(item: OutboxItem)

    /** 재시도 한도에 닿지 않은 대기 항목을 오래된 순으로 돌려준다. */
    @Query("SELECT * FROM outbox WHERE attempts < :maxAttempts ORDER BY createdAt LIMIT :limit")
    suspend fun pending(maxAttempts: Int, limit: Int): List<OutboxItem>

    @Query("DELETE FROM outbox WHERE eventId = :eventId")
    suspend fun remove(eventId: String)

    @Query("UPDATE outbox SET attempts = attempts + 1, lastError = :error WHERE eventId = :eventId")
    suspend fun markFailed(eventId: String, error: String)

    /** 수동 재전송: 실패 횟수를 초기화한다. */
    @Query("UPDATE outbox SET attempts = 0")
    suspend fun resetAttempts()

    @Query("SELECT COUNT(*) FROM outbox")
    suspend fun count(): Int

    @Query("SELECT COUNT(*) FROM outbox")
    fun observeCount(): Flow<Int>

    @Query("SELECT COUNT(*) FROM outbox WHERE attempts >= :maxAttempts")
    fun observeFailedCount(maxAttempts: Int): Flow<Int>

    @Query("SELECT * FROM seen_files WHERE path = :path")
    suspend fun seenFile(path: String): SeenFile?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun markSeen(file: SeenFile)
}
