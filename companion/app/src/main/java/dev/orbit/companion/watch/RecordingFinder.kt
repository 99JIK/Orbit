package dev.orbit.companion.watch

import android.os.Environment
import java.io.File

/**
 * 녹음 폴더 찾기. 기종·One UI 버전마다 녹음 앱 저장 위치가 달라서, 내부 저장소를 훑어
 * 오디오 파일이 있는 폴더를 최근 수정 순으로 보여 준다. 사용자가 골라 감시 폴더로 지정한다.
 */
object RecordingFinder {
    data class Candidate(val dir: File, val audioCount: Int, val latestModified: Long)

    private val AUDIO = setOf("m4a", "mp3", "ogg", "wav", "amr", "3gp", "aac")
    private const val MAX_DEPTH = 5
    private const val MAX_RESULTS = 10

    fun find(root: File = Environment.getExternalStorageDirectory()): List<Candidate> {
        val out = mutableListOf<Candidate>()
        walk(root, 0, out)
        return out.sortedByDescending { it.latestModified }.take(MAX_RESULTS)
    }

    /** 폴더 하나의 오디오 파일 수와 최근 수정 시각. 폴더가 없으면 null. */
    fun inspect(dir: File): Candidate? {
        if (!dir.isDirectory) return null
        val audio = dir.listFiles()?.filter { it.isFile && it.extension.lowercase() in AUDIO }.orEmpty()
        return Candidate(dir, audio.size, audio.maxOfOrNull { it.lastModified() } ?: 0L)
    }

    private fun walk(dir: File, depth: Int, out: MutableList<Candidate>) {
        if (depth > MAX_DEPTH) return
        val children = dir.listFiles() ?: return
        var count = 0
        var latest = 0L
        for (f in children) {
            if (f.name.startsWith(".")) continue
            if (f.isDirectory) {
                // 앱 전용 데이터 폴더는 권한이 있어도 녹음 앱 저장 위치가 아니고 양이 많다
                if (depth == 0 && f.name == "Android") continue
                walk(f, depth + 1, out)
            } else if (f.extension.lowercase() in AUDIO) {
                count++
                if (f.lastModified() > latest) latest = f.lastModified()
            }
        }
        if (count > 0) out += Candidate(dir, count, latest)
    }
}
