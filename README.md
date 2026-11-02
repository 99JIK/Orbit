# Orbit

**O**wn **R**untime, **B**ring-your-own **I**ntelligence **T**oolkit — *Your work, in your orbit.*

Orbit은 본인의 폰과 홈서버에서 돌아가는 개인 업무 비서다. 녹음·공유·알림을 로컬에서 텍스트로 바꾸고, 일정과 할 일을 추출해 디스코드 승인을 거쳐 Google Calendar/Tasks에 등록한다. 중앙 서버가 없고 프로젝트 운영비는 0이다.

## 저장소 구성

| 경로 | 내용 |
| --- | --- |
| [docs/spec.md](docs/spec.md) | 시스템 스펙 (기준 문서) |
| [docs/ingest-api.md](docs/ingest-api.md) | 수집 API 명세 |
| [docs/companion.md](docs/companion.md) | 안드로이드 앱 명세·설치 |
| `companion/` | 안드로이드 수집 앱 (Kotlin) |
| `server/` | 홈서버 (Python, FastAPI) |
| `.githooks/` | 저장소 훅 |

## 현재 상태

M1 진행 중. 구현 범위는 Companion의 공유·녹음 폴더 감시·전송 큐·하트비트와 서버의 수집 API다. STT·추출·디스코드 봇은 아직 없다.

## 개발 환경

```sh
git config core.hooksPath .githooks   # 클론 후 1회
```
