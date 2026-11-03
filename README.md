# Orbit

**O**wn **R**untime, **B**ring-your-own **I**ntelligence **T**oolkit: *Your work, in your orbit.*

Orbit은 본인의 폰과 홈서버에서 돌아가는 개인 업무 비서다. 녹음·공유·알림을 로컬에서 텍스트로 바꾸고, 일정과 할 일을 추출해 Google Calendar에 등록하고, 요약을 Obsidian 호환 볼트에 남긴다. 중앙 서버가 없고 프로젝트 운영비는 0이다.

## 저장소 구성

| 경로 | 내용 |
| --- | --- |
| [docs/spec.md](docs/spec.md) | 시스템 스펙 (기준 문서) |
| [docs/ingest-api.md](docs/ingest-api.md) | 수집 API 명세 |
| [docs/companion.md](docs/companion.md) | 안드로이드 앱 명세·설치 |
| [docs/deploy.md](docs/deploy.md) | 서버 배포 (Linux·macOS·Docker·Windows) |
| [docs/normalize-ko.md](docs/normalize-ko.md) | 한국어 날짜 정규화 규칙 |
| [docs/extract.md](docs/extract.md) | 분류·추출과 평가 |
| [docs/worker.md](docs/worker.md) | worker: STT·요약·Calendar 등록·볼트·검토 알림 |
| `companion/` | 안드로이드 수집 앱 (Kotlin) |
| `server/` | 홈서버 (Python, FastAPI) |
| `deploy/` | 플랫폼별 서비스 등록 템플릿 |

## 지원 플랫폼

| 구성 요소 | 플랫폼 |
| --- | --- |
| 서버 | macOS, Linux, Docker(NAS 포함), Windows |
| Companion | Android 11 이상 (갤럭시 One UI 기준) |

CI는 Ubuntu·macOS·Windows에서 서버 테스트를 돌리고, Docker 이미지(amd64·arm64)와 Companion APK를 빌드한다.

## 현재 상태

M1 진행 중. 구현 범위는 Companion의 공유·녹음 폴더 감시·전송 큐·하트비트, 서버의 수집 API와 worker다.

- worker: 통화·음성 녹음 로컬 STT, 본인 AI 구독 CLI(Claude Code·Codex·Gemini)로 요약·항목 추출, 날짜 정규화, Google Calendar 바로 등록과 되돌리기, 볼트 노트, 디스코드 웹훅 검토 알림 ([docs/worker.md](docs/worker.md))
- 아직 없음: 디스코드 봇(버튼·명령), Google Tasks, 맥락 사전, 검색
