package dev.orbit.companion.ui

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.widget.Toast
import androidx.core.content.IntentCompat
import dev.orbit.companion.Settings
import dev.orbit.companion.ingest.Outbox
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.UUID

/**
 * "공유 → Orbit으로 보내기" 대상 (스펙 3.1).
 * 화면 없이 입력을 큐에 넣고 바로 닫힌다. 공유 URI 권한은 일시적이므로 파일은 앱 저장소로 복사한다.
 */
class ShareActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!Settings(this).isConfigured) {
            Toast.makeText(this, "Orbit 앱에서 서버를 먼저 연결하세요", Toast.LENGTH_LONG).show()
            finish()
            return
        }

        val capturedAt = System.currentTimeMillis()
        val app = referrer?.host
        val outbox = Outbox(applicationContext)
        val shareIntent = intent

        CoroutineScope(Dispatchers.Main).launch {
            val count = withContext(Dispatchers.IO) {
                runCatching { enqueue(shareIntent, outbox, capturedAt, app) }.getOrDefault(0)
            }
            val message = if (count > 0) "Orbit으로 보냄 (${count}건)" else "보낼 내용이 없습니다"
            Toast.makeText(applicationContext, message, Toast.LENGTH_SHORT).show()
        }
        finish()
    }

    private suspend fun enqueue(intent: Intent, outbox: Outbox, capturedAt: Long, app: String?): Int {
        val uris = streamUris(intent)
        if (uris.isEmpty()) {
            val text = intent.getStringExtra(Intent.EXTRA_TEXT)?.takeIf { it.isNotBlank() } ?: return 0
            outbox.addText(text, intent.getStringExtra(Intent.EXTRA_SUBJECT), capturedAt, app)
            return 1
        }
        for (uri in uris) {
            val name = displayName(uri) ?: "shared"
            val mime = contentResolver.getType(uri) ?: intent.type ?: "application/octet-stream"
            val copy = copyToOutbox(uri, name)
            outbox.addFile(
                file = copy,
                displayName = name,
                mimeType = mime,
                origin = "share",
                capturedAt = capturedAt,
                durationMs = null,
                deleteAfterSend = true,
                app = app,
            )
        }
        return uris.size
    }

    private fun streamUris(intent: Intent): List<Uri> = when (intent.action) {
        Intent.ACTION_SEND ->
            listOfNotNull(IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java))
        Intent.ACTION_SEND_MULTIPLE ->
            IntentCompat.getParcelableArrayListExtra(intent, Intent.EXTRA_STREAM, Uri::class.java).orEmpty()
        else -> emptyList()
    }

    private fun displayName(uri: Uri): String? =
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) cursor.getString(0) else null
        }

    private fun copyToOutbox(uri: Uri, name: String): File {
        val dir = File(filesDir, "outbox").apply { mkdirs() }
        val target = File(dir, "${UUID.randomUUID()}.${name.substringAfterLast('.', "bin")}")
        contentResolver.openInputStream(uri)!!.use { input ->
            target.outputStream().use { output -> input.copyTo(output) }
        }
        return target
    }
}
