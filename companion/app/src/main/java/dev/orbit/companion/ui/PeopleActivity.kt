package dev.orbit.companion.ui

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Bundle
import android.provider.CallLog
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import dev.orbit.companion.ingest.Outbox
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 번호별 이름·관계·메모 입력 (docs/companion.md 2.4).
 *
 * 최근 통화 기록을 번호별로 보여 주고, 고른 번호에 관계·메모를 붙여 서버 사람 사전으로 보낸다.
 * 서버는 통화 녹음을 처리할 때 이 정보로 STT 힌트와 요약 맥락을 만든다.
 * 입력값은 화면에 다시 보여 주려고 기기에도 남긴다(서버가 원천).
 */
class PeopleActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val colors = if (isSystemInDarkTheme()) darkColorScheme() else lightColorScheme()
            MaterialTheme(colorScheme = colors) {
                Surface(Modifier.fillMaxSize()) { PeopleScreen() }
            }
        }
    }

    private data class Recent(val number: String, val cachedName: String, val lastAt: Long)

    @Composable
    private fun PeopleScreen() {
        val scope = rememberCoroutineScope()
        val store = remember { LocalPeople(this) }
        var refresh by remember { mutableIntStateOf(0) }
        var recents by remember { mutableStateOf<List<Recent>>(emptyList()) }
        var editing by remember { mutableStateOf<Recent?>(null) }
        var message by remember { mutableStateOf("") }
        val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { refresh++ }
        val granted = checkSelfPermission(Manifest.permission.READ_CALL_LOG) == PackageManager.PERMISSION_GRANTED

        LaunchedEffect(refresh, granted) {
            if (granted) recents = withContext(Dispatchers.IO) { recentCalls() }
        }

        Column(
            Modifier.safeDrawingPadding().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text("사람·관계 메모", style = MaterialTheme.typography.headlineSmall)
            Text(
                "번호를 눌러 이름·관계·메모를 적으면 서버로 보냅니다. 통화 녹음 정리 때 이름 인식과 요약에 씁니다.",
                style = MaterialTheme.typography.bodySmall,
            )
            OutlinedButton(onClick = { editing = Recent("", "", 0L) }) { Text("번호 직접 추가") }
            if (message.isNotEmpty()) Text(message)
            if (!granted) {
                Text("최근 통화 목록을 보려면 통화 기록 권한이 필요합니다.")
                Button(onClick = { permission.launch(Manifest.permission.READ_CALL_LOG) }) { Text("허용") }
            }
            HorizontalDivider()
            LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                items(recents, key = { it.number }) { r ->
                    val saved = store.get(r.number)
                    Column(Modifier.fillMaxWidth().clickable { editing = r }.padding(vertical = 4.dp)) {
                        val name = saved?.optString("name").orEmpty().ifEmpty { r.cachedName }
                        Text(name.ifEmpty { r.number }, style = MaterialTheme.typography.bodyLarge)
                        val relation = saved?.optString("relation").orEmpty()
                        Text(
                            listOf(r.number, relation.ifEmpty { "관계 미입력" }, "최근 ${formatTime(r.lastAt)}")
                                .joinToString(" · "),
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
            }
        }

        editing?.let { r ->
            val saved = store.get(r.number)
            EditDialog(
                initialNumber = r.number,
                initialName = saved?.optString("name").orEmpty().ifEmpty { r.cachedName },
                initialRelation = saved?.optString("relation").orEmpty(),
                initialNote = saved?.optString("note").orEmpty(),
                onDismiss = { editing = null },
                onSave = { number, name, relation, note ->
                    editing = null
                    scope.launch {
                        store.put(number, name, relation, note)
                        Outbox(applicationContext).addContact(number, name, relation, note)
                        message = "저장했습니다. 서버로 전송합니다."
                        refresh++
                    }
                },
            )
        }
    }

    @Composable
    private fun EditDialog(
        initialNumber: String,
        initialName: String,
        initialRelation: String,
        initialNote: String,
        onDismiss: () -> Unit,
        onSave: (String, String, String, String) -> Unit,
    ) {
        var number by remember { mutableStateOf(initialNumber) }
        var name by remember { mutableStateOf(initialName) }
        var relation by remember { mutableStateOf(initialRelation) }
        var note by remember { mutableStateOf(initialNote) }
        AlertDialog(
            onDismissRequest = onDismiss,
            title = { Text(if (initialNumber.isEmpty()) "번호 추가" else initialNumber) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (initialNumber.isEmpty()) {
                        OutlinedTextField(
                            value = number, onValueChange = { number = it }, label = { Text("전화번호") },
                            singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
                        )
                    }
                    OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("이름") }, singleLine = true)
                    OutlinedTextField(
                        value = relation, onValueChange = { relation = it },
                        label = { Text("관계 (예: 지도교수, 공동연구자)") }, singleLine = true,
                    )
                    OutlinedTextField(value = note, onValueChange = { note = it }, label = { Text("메모") })
                }
            },
            confirmButton = {
                TextButton(
                    enabled = number.isNotBlank(),
                    onClick = { onSave(number.trim(), name.trim(), relation.trim(), note.trim()) },
                ) { Text("저장") }
            },
            dismissButton = { TextButton(onClick = onDismiss) { Text("취소") } },
        )
    }

    /** 최근 통화를 번호별 최신 1건으로 묶는다. */
    private fun recentCalls(): List<Recent> {
        val out = LinkedHashMap<String, Recent>()
        val projection = arrayOf(CallLog.Calls.NUMBER, CallLog.Calls.CACHED_NAME, CallLog.Calls.DATE)
        contentResolver.query(
            CallLog.Calls.CONTENT_URI, projection, null, null, "${CallLog.Calls.DATE} DESC",
        )?.use { c ->
            while (c.moveToNext() && out.size < MAX_RECENT) {
                val number = c.getString(0).orEmpty()
                if (number.isBlank() || number in out) continue
                out[number] = Recent(number, c.getString(1).orEmpty(), c.getLong(2))
            }
        }
        return out.values.toList()
    }

    private fun formatTime(epochMs: Long): String =
        if (epochMs == 0L) "-" else SimpleDateFormat("M월 d일", Locale.KOREA).format(Date(epochMs))

    private companion object {
        const val MAX_RECENT = 50
    }
}

/** 화면 표시용 로컬 사본. 번호 → {name, relation, note}. */
private class LocalPeople(context: Context) {
    private val prefs = context.getSharedPreferences("people", Context.MODE_PRIVATE)

    fun get(number: String): JSONObject? = prefs.getString(key(number), null)?.let(::JSONObject)

    fun put(number: String, name: String, relation: String, note: String) {
        val json = JSONObject().apply {
            put("name", name)
            put("relation", relation)
            put("note", note)
        }
        prefs.edit().putString(key(number), json.toString()).apply()
    }

    // 서버 normalize_phone과 같은 규칙. 하이픈·국가번호 차이로 다른 사람처럼 보이지 않게 한다
    private fun key(number: String): String {
        val digits = number.filter { it.isDigit() }
        return if (digits.startsWith("82") && digits.length >= 11) "0" + digits.drop(2) else digits
    }
}
