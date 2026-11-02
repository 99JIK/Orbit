# Orbit 수집 API 명세 (v0.1)

| 항목 | 내용 |
| --- | --- |
| 상태 | M1 구현 기준 |
| 구현 | `server/orbit_server/app.py` (서버), `companion/.../ingest/IngestClient.kt` (클라이언트) |
| 상위 문서 | [spec.md](spec.md) 3.3, 4.2, 8장 |

## 1. 개요

### 1.1 목적

Companion 앱 등 수집 클라이언트가 홈서버 `ingest` 프로세스로 이벤트를 전달하는 HTTP 계약을 정의한다.

### 1.2 범위

- 포함: 이벤트·파일 수신, 하트비트, 상태 점검, 기기 인증, 중복 처리
- 제외: 파이프라인 처리(STT·추출), 디스코드 연동. `ingest`는 이벤트를 `queued` 상태로 저장하는 데까지만 책임진다

### 1.3 용어

| 용어 | 정의 |
| --- | --- |
| 기기 | 토큰 하나를 발급받은 수집 클라이언트. `device_id`로 식별한다 |
| 이벤트 | 원시 입력 1건. 2장 스키마를 따른다 |
| 수락 | 서버가 이벤트를 새로 저장한 상태 |
| 중복 | 같은 이벤트를 이미 저장해 새로 저장하지 않은 상태 |

본 문서의 "해야 한다(MUST)", "하지 않아야 한다(MUST NOT)", "권장한다(SHOULD)"는 RFC 2119의 의미를 따른다.

## 2. 이벤트 스키마

```json
{
  "event_id": "8f0c2c1e-1b2a-4c3d-9e8f-6a7b8c9d0e1f",
  "source": "phone",
  "type": "audio",
  "captured_at": "2026-11-02T15:24:00+09:00",
  "payload": { "origin": "call", "file_name": "통화 녹음 홍길동_261102_152400.m4a" },
  "dedupe_key": "3b4f…(sha256 hex)",
  "device_id": "galaxy-01"
}
```

| 필드 | 형식 | 필수 | 규칙 |
| --- | --- | --- | --- |
| `event_id` | UUID 문자열 | 예 | 클라이언트가 생성한다. 재전송 시 같은 값을 써야 한다 |
| `source` | `phone` \| `gmail` \| `github` \| `rss` \| `desktop` \| `discord` | 예 | Companion은 `phone` |
| `app` | 문자열 | 아니오 | 입력을 만든 앱의 패키지명 |
| `type` | `audio` \| `notification` \| `sms` \| `share` \| `email` \| `location` | 예 | |
| `captured_at` | ISO 8601 | 예 | UTC 오프셋을 포함해야 한다. 날짜 정규화의 기준 시각이다 |
| `payload` | 객체 | 예 | 2.1 참조 |
| `dedupe_key` | 문자열 | 예 | 내용 기반 키. 텍스트는 본문의, 파일은 파일 내용의 SHA-256 hex를 권장한다 |
| `device_id` | 문자열 | 아니오 | 참고값이다. 서버는 이 값을 신뢰하지 않고 토큰으로 기기를 정한다 |

### 2.1 payload

| 키 | 대상 | 의미 |
| --- | --- | --- |
| `title` | 공통 | 표시용 제목. 공유 제목 또는 확장자를 뺀 파일명 |
| `text` | 텍스트 | 본문. 서버는 `events.text` 컬럼에 복사한다 |
| `origin` | 파일 | `call`(통화 녹음) \| `voice`(음성 녹음) \| `share`(공유) |
| `file_name` | 파일 | 원래 파일명 |
| `mime` | 파일 | MIME 타입 |
| `size` | 파일 | 바이트 수 |
| `duration_ms` | 오디오 | 재생 길이. 측정하지 못하면 생략한다 |
| `call` | 통화 녹음 | 대응하는 통화 기록. 형식은 [companion.md](companion.md) 2.2. 찾지 못하면 생략한다 |

서버는 위 목록에 없는 키도 거부하지 않고 그대로 저장한다.

## 3. 인증

- 모든 쓰기 요청은 `Authorization: Bearer <token>` 헤더를 포함해야 한다
- 토큰은 기기마다 하나씩 발급하며 서버에는 SHA-256 해시로만 저장한다. 평문은 발급 시 한 번만 표시한다
- 토큰이 없거나, 맞지 않거나, 폐기됐으면 `401`을 반환한다
- 전송 구간 보호는 Tailscale(WireGuard)이 맡는다. 서버 포트를 tailnet 밖에 열지 않아야 한다

| 작업 | 명령 |
| --- | --- |
| 발급 | `orbit-server device add <device_id>` |
| 목록 | `orbit-server device list` |
| 폐기 | `orbit-server device revoke <device_id>` |

## 4. 엔드포인트

### 4.1 `POST /ingest`

본문이 2장 스키마인 JSON 이벤트를 수신한다.

### 4.2 `POST /ingest/file`

`multipart/form-data`로 파일 이벤트를 수신한다.

| 파트 | 내용 |
| --- | --- |
| `event` | 2장 스키마의 JSON 문자열 |
| `file` | 파일 본체. 최대 500MB |

원본은 `$ORBIT_HOME/data/raw/YYYY/MM/<event_id><확장자>`에 저장하고, `data/` 기준 상대 경로를 `events.file_ref`에 기록한다. 연·월은 `captured_at`의 UTC 기준이다. 쓰기는 임시 파일에 먼저 기록한 뒤 이름을 바꿔 완료하므로, 중단된 업로드가 원본으로 남지 않는다.

### 4.3 `POST /heartbeat`

| 필드 | 형식 | 필수 |
| --- | --- | --- |
| `listener_ok` | bool | 예 |
| `queue_len` | int | 예 |
| `app_version` | 문자열 | 아니오 |
| `at` | ISO 8601 | 아니오 (서버는 수신 시각을 기록한다) |

성공 시 `204`를 반환한다. Companion은 15분 간격으로 보낸다.

### 4.4 `GET /health`

인증 없이 호출할 수 있다. 유효한 토큰을 함께 보내면 응답에 해당 기기의 `device_id`를 포함한다. Companion은 연결 확인 시 이 값을 받아 자기 기기 ID로 저장한다.

```json
{ "status": "ok", "queue_len": 3, "last_received_at": "2026-11-02T07:19:00+00:00",
  "disk_free_gb": 188.0, "device_id": "galaxy-01" }
```

## 5. 응답과 클라이언트 동작

| 코드 | 의미 | 클라이언트 동작 |
| --- | --- | --- |
| `201` | 수락 | 큐에서 제거 |
| `200` `{"status":"duplicate"}` | 중복 | 큐에서 제거 |
| `401` | 인증 실패 | 전송을 멈추고 사용자에게 알린다. 자동 재시도하지 않아야 한다 |
| `413` | 파일 크기 초과 | 해당 항목 실패 처리 |
| `422` | 스키마 위반 | 해당 항목 실패 처리 |
| `5xx`, 네트워크 오류 | 일시 장애 | 지수 백오프로 재시도 |

### 5.1 중복 판정

`event_id` 또는 `dedupe_key`가 같은 이벤트가 이미 있으면 중복으로 본다. 응답의 `event_id`는 기존 이벤트의 ID다. 이 규칙으로 다음 두 경우가 모두 한 건으로 합쳐진다.

- 응답 유실로 같은 이벤트를 재전송한 경우 (`event_id` 일치)
- 같은 파일을 다른 경로로 다시 보낸 경우. 예: 자동 감시로 올라간 녹음을 공유하기로 다시 보낸 경우 (`dedupe_key` 일치)

## 6. 서버 실행

```sh
cd server
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
orbit-server device add galaxy-01      # 토큰 출력
orbit-server serve --port 8787          # 0.0.0.0:8787
pytest                                  # 테스트
```

데이터 위치는 환경 변수 `ORBIT_HOME`으로 정한다(기본값 `~/orbit`). 디렉터리 구조는 [spec.md](spec.md) 4.4를 따른다.
