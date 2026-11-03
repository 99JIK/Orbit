# MCP 서버 명세 (v0.1)

| 항목 | 내용 |
| --- | --- |
| 상태 | 읽기 전용 도구 6개 구현 |
| 구현 | `server/orbit_server/mcp_server.py`, `index.py` |
| 서비스 | `deploy/systemd/orbit-mcp.service` |
| 상위 문서 | [spec.md](spec.md) 18.4, 18.7 방식 B / [worker.md](worker.md) |

## 1. 개요

Claude Code, Codex, Gemini CLI, Claude Desktop 같은 AI 도구가 Orbit 기록을 찾아 쓰게 하는 MCP 서버다. AI는 볼트 파일을 직접 읽지 않고, 이 서버의 도구로 필요한 조각만 받는다 (spec 18.7).

- 전송: streamable HTTP (`http://<host>:8788/mcp`)
- 인증: 클라이언트별 Bearer 토큰. 해시만 저장한다
- 쓰기 도구 없음. 노트 제안(`propose_note`)은 승인 화면(디스코드 봇)이 생긴 뒤에 붙인다

## 2. 도구

| 도구 | 인자 | 결과 |
| --- | --- | --- |
| `search_notes` | `query`, `limit`(8), `type`(call·voice·share·person), `since`, `until`(YYYY-MM-DD) | 검색어가 모두 들어 있는 문단을 노트별로 하나씩: path, 제목, 종류, 작성 시각, 절, 발췌 |
| `read_note` | `path` | 노트 전문. 볼트 밖 경로와 `.md`가 아닌 파일은 거부 |
| `recent_notes` | `days`(7), `type`, `limit`(20) | 최근 노트 목록 |
| `get_person` | `query`(이름·별칭·전화번호) | 관계·소속·메모·연락처와 그 사람이 링크된 노트 목록 |
| `list_people` | 없음 | 사람 사전 전체 |
| `list_events` | `start`, `end`(기본 오늘부터 14일) | worker가 뽑은 일정·마감과 Calendar 등록 상태, 날짜 없는 할 일 |

서버 안내문(instructions)으로 근거 노트의 path를 출처로 밝히고, 노트 내용 안의 지시는 따르지 않게 한다 (spec 9.1).

## 3. 검색

- 색인: `data/index.db` (spec 8.2 파생물). 검색할 때마다 수정 시각이 바뀐 노트만 다시 색인한다. 지워도 다시 만든다(`orbit-server reindex`)
- 단위: `## ` 절을 나누고 800자씩 자른 문단. 노트 제목도 한 조각으로 넣는다
- 한국어: FTS5 `trigram` 토크나이저. 3글자 이상 검색어는 FTS5로, 그보다 짧은 검색어(회의, 마감 등)는 LIKE로 거른다. 순위는 FTS5 검색어가 있으면 bm25, 없으면 최신순
- 벡터 검색과 순위 결합(spec 18.4 3~4단계)은 아직 없다

## 4. 범위

클라이언트 토큰마다 범위를 정한다.

| 범위 | 볼 수 있는 것 |
| --- | --- |
| `summary` (기본) | 노트의 요약·일정·사람·원본 자료. 통화·음성 메모의 `## 전사` 절은 검색하지 않고 `read_note`에서도 가린다 |
| `full` | 전사문까지 |

원 녹취에는 상대방이 한 말이 그대로 들어 있다. 붙이는 AI는 모두 외부 제공사(spec 19.1의 remote)이므로 기본을 요약까지로 둔다. 이 기본값은 spec 9.3의 취지를 따른 판단이다.

## 5. 설치와 연결

### 5.1 서버

```sh
pip install ".[mcp]"
orbit-server mcp --host 127.0.0.1 --port 8788
orbit-server mcp-client add claude-code            # 범위 summary
orbit-server mcp-client add laptop --scope full    # 원 녹취 포함
orbit-server mcp-client list
orbit-server mcp-client revoke laptop
```

- 기본은 `127.0.0.1`(같은 PC의 AI 도구만). tailnet의 다른 기기에서 쓰려면 Tailscale 주소로 연다. tailnet 밖에 열지 않는다
- 서비스 등록: `deploy/systemd/orbit-mcp.service`

### 5.2 AI 도구

토큰은 `mcp-client add`가 한 번만 보여 준다.

```sh
# Claude Code
claude mcp add --transport http orbit http://127.0.0.1:8788/mcp --header "Authorization: Bearer <토큰>"
# Codex CLI (~/.codex/config.toml)
[mcp_servers.orbit]
url = "http://127.0.0.1:8788/mcp"
bearer_token_env_var = "ORBIT_MCP_TOKEN"
# Gemini CLI (~/.gemini/settings.json)
{"mcpServers": {"orbit": {"httpUrl": "http://127.0.0.1:8788/mcp", "headers": {"Authorization": "Bearer <토큰>"}}}}
```

Claude Code 외의 설정 형식은 각 CLI 문서 기준이며 이 저장소에서 실제로 연결해 보지는 않았다.

Claude 웹·모바일(claude.ai 커넥터)은 Anthropic 서버가 MCP 서버에 직접 접속하므로 인터넷에 열어야 한다. 기록을 내부망에만 두는 원칙(spec 1.5, 9.3)과 맞지 않아 지원하지 않는다.

## 6. 확인 결과

| 날짜 | 클라이언트 | 결과 |
| --- | --- | --- |
| 2026-11-03 | Claude Code 2.1 (`--mcp-config`, summary 범위) | 합성 통화 노트에서 "김민수 교수님과 정한 회의와 이번 달 마감"을 물어 11-10 14:00 공대 9호관 401호, 11-06 실험 데이터 제출을 출처 노트와 함께 답함 |
