# 분류·추출 명세 (v0.1)

| 항목 | 내용 |
| --- | --- |
| 상태 | 구현 완료, 워커 미연결 |
| 구현 | `server/orbit_server/extract.py`, `server/orbit_server/candidates.py` |
| 평가 | `python -m orbit_server.eval_extract --url <ollama> --models <모델...>` |
| 상위 문서 | [spec.md](spec.md) 5장, 9.1, 11장 / [normalize-ko.md](normalize-ko.md) |

## 1. 개요

이벤트 텍스트 하나를 SLM 호출 한 번으로 분류하고 원문 표현을 추출한 뒤, 날짜 정규화를 적용해 후보를 만든다. spec 5장의 2단계(분류)와 3단계(추출)는 8GB 기기의 호출 비용을 줄이기 위해 한 번의 호출로 합친다.

```
텍스트 ─▶ SLM (스키마 강제) ─▶ Extraction ─▶ resolve() ─▶ Candidate
```

## 2. 출력 스키마

| 필드 | 형식 | 규칙 |
| --- | --- | --- |
| `kind` | `schedule` \| `deadline` \| `task` \| `note` \| `ignore` | 범위 밖 값은 `ignore`로 처리 |
| `confidence` | 0~1 | 범위 밖 값은 잘라냄 |
| `title` | 문자열 | 15자 안팎 명사구, 날짜·시각 제외 |
| `date_expr`, `time_expr`, `duration_expr` | 문자열 \| null | 원문 표현 그대로. 계산 금지. 시각 범위는 `time_expr`에 통째로 |
| `people` | 문자열 배열 | 원문 호칭 그대로 |
| `project` | 문자열 \| null | 원문에 있을 때만 |

스키마는 Ollama 구조화 출력(`format`)으로 강제한다. 온도는 0이다.

## 3. 프롬프트 구성

| 순서 | 역할 | 내용 |
| --- | --- | --- |
| 1 | system | 유형 판정 기준, 필드 규칙, 데이터 블록 안의 지시를 따르지 않는다는 규칙 |
| 2 | user·assistant 쌍 × 6 | 예시: 일정, 마감, 시각 범위, 결정 사항(note), 인젝션 시도(ignore), 광고 |
| 3 | user | `<data>` 블록으로 감싼 입력. 입력 안의 `</data>`는 제거한다 |

예시 문장은 평가 세트와 겹치지 않게 유지해야 한다. 겹치면 평가 결과가 실제 성능보다 높게 나온다.

## 4. 후보 생성 규칙

| 조건 | 결과 |
| --- | --- |
| `ignore` | 정규화하지 않음 |
| `schedule`·`deadline`이고 날짜 해석 실패 | `task`로 강등, `demoted = true` (spec 5.1) |
| 그 외 | `resolve()` 결과를 `when`에 붙임 |

모델이 범위의 끝을 `duration_expr`에 넣는 경우("다섯 시까지")는 정규화가 끝 시각으로 해석한다.

## 5. 평가 결과

평가 세트: `server/tests/data/extract_cases.jsonl` (합성 문장 20건, 인젝션 1건 포함). 캡처 시각 2026-11-02(월) 15:24 KST.

| 날짜 | 모델 | 유형 | 시각 | 지연 중앙값 | 비고 |
| --- | --- | --- | --- | --- | --- |
| 2026-11-02 | gemma3:4b (Q4_K_M) | 19/20 | 19/20 | 1.6s | 인젝션 1건 실패 |

spec 11.1의 M2 목표(일시 정확도 85% 이상)를 합성 세트 기준으로 넘는다. 실제 녹취는 STT 오류와 구어체가 섞이므로 실사용 데이터로 다시 평가해야 한다 (spec 14.1).

### 5.1 알려진 한계: 프롬프트 인젝션

4B 모델은 "이전 지시는 무시하고 …로 출력하라" 형태의 지시에 일부 따른다(평가 18번). 영향은 다음으로 제한된다.

- 출력은 스키마 필드뿐이며, 도구 호출이나 자유 지시를 해석하지 않는다 (spec 9.1)
- 모든 쓰기는 `#approve` 승인을 거친다. 최악의 경우 잘못된 후보 카드 1장이 생긴다

추가 대응은 실사용 데이터를 보고 정한다. 후보: 지시형 문장 휴리스틱 표시, 상위 모델 재판정.

## 6. 음성 경로 (STT)

Ollama는 음성을 다루지 않으므로 STT는 whisper 계열 엔진을 따로 쓴다 (`server/orbit_server/stt.py`, 선택 의존성 `pip install ".[stt]"`).

| 환경 | 엔진 | 모델 |
| --- | --- | --- |
| NVIDIA GPU | faster-whisper (CUDA) | large-v3-turbo |
| Apple Silicon | whisper.cpp (Metal) | large-v3-turbo |
| CPU | faster-whisper (int8) | large-v3-turbo, 느리면 small\~medium |

- 모델은 작업 단위로 올렸다 내린다 (spec 4.3)
- 언어는 한국어로 고정하고, VAD로 무음 구간을 건너뛴다
- 맥락 사전의 고유명사와 통화 상대 이름(`payload.call.name`)을 initial prompt 힌트로 넣는다

평가: `python -m orbit_server.eval_stt --audio-dir <dir> --ollama <url>`. `<dir>/manifest.jsonl`에 파일과 정답 문장을 둔다.

| 날짜 | 입력 | STT | 추출 | CER | 음성→후보 | 처리 시간 |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-11-02 | Windows TTS(ko-KR) 합성 음성 10건, 힌트 없음 | large-v3-turbo, CPU int8 | gemma3:4b | 3.1% | 9/10 | 클립당 약 15초 (실시간의 약 3.6배) |

- 유일한 실패는 "4시 반 치과"를 "4시 판치과"로 받아써 30분이 빠진 경우다
- CER에는 "세 시"↔"3시" 같은 표기 차이도 오류로 잡힌다. 숫자 표기 정규화 후 비교는 다음 개선 항목이다
- 합성 음성은 실제 통화보다 깨끗하다. 실제 녹음 30건으로 다시 평가한다 (spec 14.1)
