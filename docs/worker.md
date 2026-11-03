# worker 명세 (v0.1)

| 항목 | 내용 |
| --- | --- |
| 상태 | 통화·음성 녹음과 공유 입력 처리 구현 |
| 구현 | `server/orbit_server/worker.py`, `digest.py`, `llm.py`, `gcal.py`, `vault.py`, `notify.py`, `content.py` |
| 서비스 | `deploy/systemd/orbit-worker.service` |
| 상위 문서 | [spec.md](spec.md) 4.1, 5장, 6장, 7.1, 18.1, 19장 / [extract.md](extract.md) / [deploy.md](deploy.md) |

## 1. 개요

`worker`는 `ingest`가 큐에 넣은 이벤트를 하나씩 처리한다. 지금 처리하는 유형은 `audio`(통화·음성 녹음)와 `share`(공유 텍스트·파일)이고, 나머지 유형은 큐에 남겨 둔다.

```
events(queued) ─▶ STT ─▶ 요약·항목 추출(모델) ─▶ 날짜 정규화 ─▶ Calendar 등록 ─▶ 볼트 노트 ─▶ 검토 알림
```

| 단계 | 담당 | 결과 저장 |
| --- | --- | --- |
| STT | faster-whisper, 로컬 ([extract.md](extract.md) 6장) | `events.text` |
| 요약·항목 추출 | `summarize` 역할 모델 (3장) | `digests` |
| 정규화 | `normalize.py` | `candidates` |
| 등록 | `gcal.py` | `decisions`, Google Calendar |
| 노트 | `vault.py` | `$ORBIT_HOME/vault/00 Inbox/` |
| 알림 | `notify.py` | 디스코드 웹훅 또는 표준 출력 |

### 1.1 데이터 경계

- 녹음 원본은 이 기기를 떠나지 않는다. STT는 로컬에서만 돌린다
- 모델에는 전사문과 공유 자료의 텍스트만 보낸다. 이미지와 텍스트 없는 PDF는 첨부로 보낸다
- 공유 링크는 서버가 직접 페이지를 받아 텍스트만 넘긴다

### 1.2 요약·항목 추출

[extract.md](extract.md)의 추출은 메모 한 줄에서 후보 하나를 낸다. 통화 한 통에는 약속·마감·할 일이 여럿 섞여 있으므로, worker는 같은 규칙으로 항목 배열을 받는 `digest.py`를 쓴다.

- 모델은 원문 표현(`date_expr`, `time_expr`, `duration_expr`)만 뽑고 날짜를 계산하지 않는다. 계산은 `normalize.py`가 캡처 시각 기준으로 한다 (spec 5장 4단계)
- 한 번의 호출로 제목, 요약, 핵심, 등장인물, 태그, 항목 목록을 받는다
- 일정은 합의되거나 확정된 것만 뽑게 한다. 이 기준은 프롬프트로만 정하므로 모델이 틀릴 수 있다

## 2. 설정 (`config/worker.yaml`)

예시: [`deploy/config/worker.example.yaml`](../deploy/config/worker.example.yaml). 파일이 없으면 모든 값이 기본값이다.

| 키 | 기본값 | 의미 |
| --- | --- | --- |
| `whisper_model` | `large-v3-turbo` | STT 모델 |
| `whisper_device`, `whisper_compute` | `auto` | `cuda`·`cpu`, `int8_float16`·`int8` 등 |
| `unload_after_idle_s` | 300 | 이 시간 동안 녹음이 없으면 STT 모델을 내린다 (spec 4.3) |
| `calendar_enabled` | `false` | `false`거나 토큰이 없으면 dry-run |
| `calendar_id` | `primary` | 등록할 캘린더 |
| `min_confidence` | 0.8 | 바로 등록하는 확신도 하한. spec 5.1의 승인 큐 기준값을 그대로 쓴다 |
| `calendar_from_share` | `false` | 공유 자료 속 일정도 등록할지 |
| `notify` | `stdout` | `stdout` \| `discord_webhook` |
| `vault_dir` | `$ORBIT_HOME/vault` | 볼트 위치 |
| `include_transcript` | `true` | 통화 노트에 전사문을 접어서 넣을지 |
| `max_attempts`, `retry_after_s` | 3, 600 | 실패 시 재시도 횟수와 간격 |

비밀값은 `$ORBIT_HOME/secrets/`에 둔다. 저장소에는 넣지 않는다.

| 파일 | 내용 |
| --- | --- |
| `secrets/google-client.json` | 사용자 본인 GCP 프로젝트의 OAuth 클라이언트 (4장) |
| `secrets/google-token.json` | `orbit-server auth google`이 만든 토큰. 권한 0600 |
| `secrets/discord-webhook.txt` | 디스코드 웹훅 URL. 환경 변수 `ORBIT_DISCORD_WEBHOOK_URL`로 대신할 수 있다 |

OS 키체인 연동(spec 4.7)은 아직 없다. 지금은 권한 0600 평문 파일이다.

## 3. 모델 (`config/llm.yaml`)

예시: [`deploy/config/llm.example.yaml`](../deploy/config/llm.example.yaml). 형식은 spec 19.3이다. 파일이 없으면 `claude` CLI 하나를 `summarize`·`extract`에 쓰고 하루 50회로 제한한다.

| `kind` | 대상 | 신뢰 등급 |
| --- | --- | --- |
| `cli` | `claude`(Claude Code), `codex`(Codex CLI), `gemini`(Gemini CLI) | remote |
| `ollama` | 로컬 Ollama | local |

CLI 어댑터 실행 규칙 (spec 19.2):

| 항목 | 규칙 |
| --- | --- |
| 로그인 | 사용자가 그 PC에서 CLI에 직접 로그인한다. Orbit은 키나 세션을 저장하지 않는다 |
| 작업 폴더 | 매번 새로 만든 빈 임시 폴더 |
| 도구 | 모두 끈다. 첨부가 있을 때만 파일 읽기를 허용한다 (`claude --tools Read`, `codex --sandbox read-only`) |
| 출력 | JSON 스키마 강제(`claude --json-schema`, `codex --output-schema`). `gemini`는 프롬프트로 요구하고 결과를 파싱한다 |
| 시간 제한 | 120초 (`timeout_s`로 조정) |
| 동시 실행 | 한 건씩 |
| 한도 | `limits.<provider>.daily_calls`. 넘거나 CLI가 한도 초과를 알리면 다음 후보로 넘어간다 |

- snap으로 설치한 Claude Code는 실행 파일명이 `claude-code`다. 어댑터가 자동으로 찾는다
- **Gemini 무료 등급은 입력을 제품 개선에 쓸 수 있다** (spec 19.5). 통화 전사문이 들어가는 구성에는 유료 등급이나 학습 제외가 보장되는 계정을 쓴다
- 구독은 개인 1인용이다. 다른 사람의 작업에 쓰지 않는다 (spec 4.6, 19.5)
- 끝까지 실제로 확인한 CLI는 `claude`(Claude Code 2.1)다. `codex`·`gemini`는 각 CLI 문서의 옵션으로 명령을 만들고 가짜 CLI로만 시험했다. 처음 쓸 때 한 건을 직접 돌려 확인한다

## 4. Google Calendar

### 4.1 등록 정책

승인을 기다리지 않고 바로 등록한 뒤 검토 알림을 한 번 보낸다(2026-11-03 결정, spec 6.2).

| 조건 | 처리 |
| --- | --- |
| 일정·마감, 날짜 해석 성공, 확신도 ≥ `min_confidence` | 바로 등록 |
| 일정·마감, 확신도 미달 | 등록하지 않고 알림에 "확인 필요"로 표시 |
| 일정·마감, 날짜 해석 실패 | 할 일로 강등 (spec 5.1). 노트와 알림에만 적는다 |
| 할 일·메모 | 노트와 알림에만 적는다. Google Tasks 연동은 미정 (spec 14.1) |
| 공유 자료 속 일정 | 기본은 노트에만 적는다 (`calendar_from_share`) |

- 시각이 없는 마감은 종일 일정으로 넣는다
- 기본값으로 채운 값(오후 표시 없는 시각, 기본 60분 등)은 설명란과 알림에 "추정"으로 표시한다

### 4.2 중복과 되돌리기

- Google event id는 후보 id(`이벤트 id + 순번 + 유형 + 제목`의 해시)로 정한다. 같은 이벤트를 다시 처리하면 같은 Google 일정을 갱신한다
- `extendedProperties.private.orbit_id`에 후보 id를 넣는다 (spec 7.1)
- 사용자가 캘린더에서 지운 일정과 `undo`로 되돌린 후보는 재처리해도 다시 넣지 않는다
- 되돌리기: `orbit-server undo <후보 id 앞 12자리>` 또는 `orbit-server undo <event_id>`(그 이벤트의 일정 전부). 기록은 `decisions`에 `undo`로 남는다

### 4.3 연결 절차

1. [Google Cloud Console](https://console.cloud.google.com/)에서 본인 프로젝트를 만들고 **Google Calendar API**를 사용 설정한다
2. OAuth 동의 화면: 사용자 유형 외부, 본인 계정을 테스트 사용자로 추가한 뒤 게시 상태를 **프로덕션**으로 바꾼다. 테스트 상태면 refresh token이 7일 뒤 만료된다 (spec 7.1)
3. 사용자 인증 정보에서 OAuth 클라이언트 ID(데스크톱 앱)를 만들고 JSON을 `$ORBIT_HOME/secrets/google-client.json`으로 저장한다
4. `orbit-server auth google`을 실행해 브라우저에서 동의한다. 미인증 앱 경고는 고급 > 이동으로 넘긴다(본인이 만든 앱)
5. `config/worker.yaml`에 `calendar_enabled: true`를 넣고 worker를 다시 시작한다

요청 스코프는 `calendar.events` 하나다. 충돌 검사용 `calendar.freebusy`(spec 5장 5단계)는 아직 쓰지 않는다.

## 5. 볼트

- 새 노트는 `00 Inbox/`에만 만든다. 기존 노트는 고치지 않는다 (spec 18.1)
- 파일명: `YYYY-MM-DD HHMM [상대 이름] 제목.md`. 한글 3바이트를 고려해 180바이트에서 자른다
- frontmatter: `type`(call·voice·share), `created`, `contact`, `people`(`[[이름]]`), `tags`, `source`, `event_id`
- 같은 이벤트를 다시 처리하면 `event_id`로 같은 노트를 찾아 덮어쓴다
- 할 일·마감은 `- [ ]` 체크박스로 적는다
- 통화·음성 메모의 전사문은 접힌 callout으로 넣는다. 본인이 참여한 통화의 STT는 볼트 전문 제한 대상이 아니다 (spec 9.4)
- 공유 파일 원본은 노트 옆에 `<노트 이름> (원본).<확장자>`로 복사한다. 사용자가 직접 공유한 자료라서 볼트 보존 제한(spec 9.4)의 예외로 둔다

## 6. 검토 알림

디스코드 봇(spec 6장)이 생기기 전까지는 웹훅으로 보낸다. 웹훅은 단방향이라 버튼이 없다.

```
**[Orbit] 통화 정리: 다음 회의 일정 확정**
노트: `00 Inbox/2026-11-03 1430 김민수 다음 회의 일정 확정.md`

캘린더 등록 2건
- 2026-11-10 14:00-15:00 일정 **연구 회의** (추정 포함)
  수정·삭제: <https://www.google.com/calendar/event?eid=...>
  되돌리기: `orbit-server undo a59b8dde5b5f`
- 2026-11-06 (종일) 마감 **실험 데이터 메일 제출**
  ...
노트에만 적음
- 할 일 발표 자료 준비
```

수정·삭제 링크는 폰에서 Google Calendar 앱으로 열린다. 알림 전송이 실패해도 처리는 완료로 둔다.

## 7. 사람 사전

번호로 통화 상대를 알아보고, 이름·관계를 STT 힌트와 요약 맥락에 넣는다 (spec 8.3 `people`).

| 원천 | 내용 | 우선 |
| --- | --- | --- |
| `config/context.yaml`의 `people` | 사용자가 직접 적는 항목(`name`, `aliases`, `role`, `phones`, `org`, `title`, `note`) | 높음 |
| 운영 DB `people` | 앱의 관계 메모(`contact` 이벤트), 명함에서 뽑은 연락처 | 빈 칸만 채움 |

- 같은 사람 판정: 번호가 같으면 같은 사람, 번호가 없으면 이름(또는 별칭)이 같으면 같은 사람. 번호는 숫자만 남기고 `+82`를 `0`으로 바꿔 비교한다
- 앱에서 이름을 바꾸면 옛 이름은 별칭으로 남는다. 예전 노트의 `[[옛 이름]]`을 정식 이름으로 맞추는 데 쓴다
- 통화 녹음: `payload.call.number`로 사람을 찾으면 통화 기록의 캐시 이름 대신 사전 이름을 쓰고, 이름·별칭·소속을 STT initial prompt에, `이름 (관계, 소속 직함) 메모`를 요약 프롬프트 맥락에 넣는다. 노트의 `people` 링크는 정식 이름으로 맞춘다
- `contact` 이벤트는 모델을 부르지 않는다. 사전에 반영하고 사람 노트를 만든다
- 명함: 공유 자료 요약 스키마에 `card`를 둔다. 모델이 명함으로 판단하면 이름·소속·직함·전화·이메일을 사전에 넣고, 공유 노트에 연락처 줄을 적는다
- 사람 노트: 볼트 어디에도 `<이름>.md`가 없을 때만 `00 Inbox/<이름>.md`를 새로 만든다(frontmatter `type: person`, `aliases`, `relation`, `org`, `phones` 등). 이후 관계·메모가 바뀌어도 노트는 고치지 않는다 (spec 18.1). 통화 노트의 `[[이름]]`이 이 노트의 백링크가 된다
- 확인: `orbit-server people`

## 8. 재시도와 멱등

| 상황 | 처리 |
| --- | --- |
| 처리 성공 | `status = processed`. 다시 잡지 않는다 |
| 실패 | `status = failed`, `error`에 사유. `retry_after_s` 뒤 다시 시도, `max_attempts`에서 멈춘다 |
| 재시도 | 전사문(`events.text`)과 요약(`digests`)을 다시 쓴다. STT와 모델을 다시 부르지 않는다 |
| 서비스 재시작 중 끊긴 처리 | 시작할 때 `failed`로 돌려 재시도 대상에 넣는다 |
| `orbit-server reprocess <event_id>` | 요약·추출부터 다시 한다. 전사문은 유지한다 |
| `orbit-server add <파일>` | 파일 해시를 `dedupe_key`로 써서 같은 파일은 한 번만 들어간다 |

## 9. 명령

| 명령 | 동작 |
| --- | --- |
| `orbit-server worker [--once]` | 큐 처리. `--once`는 쌓인 것만 처리하고 종료 |
| `orbit-server add <파일...> [--call 이름] [--at ISO]` | 이 PC의 파일을 큐에 넣는다 (spec 7.3). 오디오는 녹음, 그 밖은 공유 |
| `orbit-server reprocess <event_id>` | 다시 처리 |
| `orbit-server undo <id>` | 자동 등록한 일정 삭제 |
| `orbit-server status [-n 20]` | 최근 이벤트 상태와 실패 사유 |
| `orbit-server auth google` | Google Calendar 연결 |
| `orbit-server people` | 사람 사전 보기 |

## 10. 확인 결과

| 날짜 | 입력 | 환경 | 결과 |
| --- | --- | --- | --- |
| 2026-11-03 | 합성 음성 통화 43초(edge-tts 2화자, 실제 인물 아님)를 `/ingest/file`로 전송 | GTX 1660 Ti, large-v3-turbo, `claude` CLI | STT 2.9초. 일정 "다음 주 화요일 오후 2시"를 11-10 14:00, 마감 "이번 주 금요일"을 11-06 종일로 해석(캡처 11-03 화). 할 일 1건. dry-run 등록, 노트 생성 |
| 2026-11-03 | 합성 세미나 공지 마크다운 (`orbit-server add`) | 같음 | 노트와 원본 복사본 생성, 일정 2건은 노트에만 기록 |

- STT가 "9호관"을 "구호관"으로 받아 적었고 그대로 장소에 들어갔다. 숫자·고유명사 오류는 맥락 사전 힌트(spec 8.3)로 줄이는 것이 다음 항목이다
- 실제 통화 녹음으로는 아직 평가하지 않았다 (spec 14.1)
- STT 디코딩: faster-whisper 1.2.1의 내장 디코더는 PyAV 19와 맞지 않아(`metadata_errors` 인자) `stt.py`가 직접 디코딩해 파형을 넘긴다
