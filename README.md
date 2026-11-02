# Orbit

**O**wn **R**untime, **B**ring-your-own **I**ntelligence **T**oolkit — *Your work, in your orbit.*

Orbit은 본인의 폰과 홈서버에서 돌아가는 개인 업무 비서다. 녹음·공유·알림을 로컬에서 텍스트로 바꾸고, 일정과 할 일을 추출해 디스코드 승인을 거쳐 Google Calendar/Tasks에 등록한다. 중앙 서버가 없고 프로젝트 운영비는 0이다.

## 저장소 구성

| 경로 | 내용 |
| --- | --- |
| [docs/spec.md](docs/spec.md) | 시스템 스펙 (기준 문서) |
| [docs/ingest-api.md](docs/ingest-api.md) | 수집 API 명세 |
| [docs/companion.md](docs/companion.md) | 안드로이드 앱 명세·설치 |
| [docs/deploy.md](docs/deploy.md) | 서버 배포 (Linux·macOS·Docker·Windows) |
| [docs/normalize-ko.md](docs/normalize-ko.md) | 한국어 날짜 정규화 규칙 |
| [docs/extract.md](docs/extract.md) | 분류·추출과 평가 |
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

M1 진행 중. 구현 범위는 Companion의 공유·녹음 폴더 감시·전송 큐·하트비트와 서버의 수집 API다. STT·추출·디스코드 봇은 아직 없다.
