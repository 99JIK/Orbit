package dev.orbit.companion.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.PowerManager
import android.provider.Settings as SystemSettings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.orbit.companion.Settings
import dev.orbit.companion.data.OrbitDatabase
import dev.orbit.companion.ingest.IngestClient
import dev.orbit.companion.service.CompanionService
import dev.orbit.companion.work.HeartbeatWorker
import dev.orbit.companion.work.UploadWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 설정·상태 화면 (스펙 3.4): 서버 연결, 권한, 수집 토글, 큐 상태. */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val settings = Settings(this)
        setContent {
            val colors = if (isSystemInDarkTheme()) darkColorScheme() else lightColorScheme()
            MaterialTheme(colorScheme = colors) {
                Surface(Modifier.fillMaxSize()) {
                    MainScreen(settings)
                }
            }
        }
    }

    @Composable
    private fun MainScreen(settings: Settings) {
        val scope = rememberCoroutineScope()
        val dao = remember { OrbitDatabase.get(this).dao() }
        val pending by dao.observeCount().collectAsStateWithLifecycle(0)
        val failed by dao.observeFailedCount(UploadWorker.MAX_ATTEMPTS).collectAsStateWithLifecycle(0)

        var serverUrl by remember { mutableStateOf(settings.serverUrl) }
        var token by remember { mutableStateOf(settings.token) }
        var connection by remember { mutableStateOf(if (settings.deviceId.isEmpty()) "미확인" else "기기 ${settings.deviceId}") }
        var collecting by remember { mutableStateOf(settings.collecting) }
        var wifiOnly by remember { mutableStateOf(settings.wifiOnlyAudio) }
        // 화면 복귀 시 권한·전송 상태를 다시 읽기 위한 갱신 키
        var refresh by remember { mutableIntStateOf(0) }
        LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { refresh++ }

        val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { refresh++ }

        Column(
            Modifier
                .safeDrawingPadding()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Orbit Companion", style = MaterialTheme.typography.headlineSmall)

            Section("서버")
            OutlinedTextField(
                value = serverUrl,
                onValueChange = { serverUrl = it },
                label = { Text("주소 (예: http://homeserver:8787)") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = token,
                onValueChange = { token = it },
                label = { Text("기기 토큰") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth(),
            )
            Button(onClick = {
                settings.serverUrl = serverUrl
                settings.token = token
                connection = "확인 중…"
                scope.launch {
                    connection = checkConnection(settings)
                    if (settings.isConfigured) HeartbeatWorker.schedule(this@MainActivity)
                }
            }) { Text("저장 및 연결 확인") }
            Text("연결: $connection")

            Section("권한")
            refresh.let {
                PermissionRow("모든 파일 접근 (녹음 폴더)", Environment.isExternalStorageManager()) {
                    startActivity(appSettingsIntent(SystemSettings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION))
                }
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    PermissionRow("알림", hasPermission(Manifest.permission.POST_NOTIFICATIONS)) {
                        permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                    }
                }
                PermissionRow("통화 기록 (녹음에 상대 정보 첨부)", hasPermission(Manifest.permission.READ_CALL_LOG)) {
                    permissionLauncher.launch(Manifest.permission.READ_CALL_LOG)
                }
                PermissionRow("배터리 최적화 예외", isIgnoringBatteryOptimizations()) {
                    startActivity(appSettingsIntent(SystemSettings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS))
                }
            }

            Section("수집")
            ToggleRow("통화·음성 녹음 자동 전송", collecting) { on ->
                if (on && (!settings.isConfigured || !Environment.isExternalStorageManager())) {
                    connection = "서버 연결과 모든 파일 접근 권한이 먼저 필요합니다"
                    return@ToggleRow
                }
                collecting = on
                settings.collecting = on
                if (on) CompanionService.tryStart(this@MainActivity) else CompanionService.stop(this@MainActivity)
            }
            ToggleRow("녹음은 Wi-Fi에서만 전송", wifiOnly) { on ->
                wifiOnly = on
                settings.wifiOnlyAudio = on
            }
            settings.watchedDirs.forEach { Text("· ${it.dir.path}", style = MaterialTheme.typography.bodySmall) }

            Section("상태")
            refresh.let {
                Text("전송 대기 ${pending}건 · 재시도 중단 ${failed}건")
                Text("마지막 전송: ${formatTime(settings.lastSentAt)}")
                if (settings.lastError.isNotEmpty()) {
                    Text("최근 오류: ${settings.lastError}", color = MaterialTheme.colorScheme.error)
                }
            }
            OutlinedButton(onClick = {
                scope.launch {
                    dao.resetAttempts()
                    UploadWorker.schedule(this@MainActivity)
                    refresh++
                }
            }) { Text("지금 전송") }
        }
    }

    private suspend fun checkConnection(settings: Settings): String {
        if (!settings.isConfigured) return "주소와 토큰을 입력하세요"
        return withContext(Dispatchers.IO) {
            try {
                val (code, body) = IngestClient(settings.serverUrl, settings.token).health()
                val deviceId = body?.optString("device_id").orEmpty()
                when {
                    code == 200 && deviceId.isNotEmpty() -> {
                        settings.deviceId = deviceId
                        "연결됨 · 기기 $deviceId"
                    }
                    code == 200 -> "서버는 응답했지만 토큰이 유효하지 않습니다"
                    else -> "서버 응답 HTTP $code"
                }
            } catch (e: IOException) {
                "연결 실패: ${e.message}"
            } catch (e: IllegalArgumentException) {
                "주소 형식 오류: ${e.message}"
            }
        }
    }

    private fun appSettingsIntent(action: String) = Intent(action, Uri.parse("package:$packageName"))

    private fun hasPermission(permission: String) =
        checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

    private fun isIgnoringBatteryOptimizations() =
        getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)

    private fun formatTime(epochMs: Long): String =
        if (epochMs == 0L) "없음" else SimpleDateFormat("M월 d일 HH:mm", Locale.KOREA).format(Date(epochMs))
}

@Composable
private fun Section(title: String) {
    HorizontalDivider()
    Text(title, style = MaterialTheme.typography.titleMedium)
}

@Composable
private fun PermissionRow(label: String, granted: Boolean, onRequest: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(if (granted) "✓ $label" else "✗ $label", Modifier.weight(1f))
        if (!granted) OutlinedButton(onClick = onRequest) { Text("허용") }
    }
}

@Composable
private fun ToggleRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange)
    }
}
