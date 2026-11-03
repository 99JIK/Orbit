import json
import os

import httpx
import pytest

from orbit_server import db
from orbit_server.index import Index, strip_sections
from orbit_server.mcp_server import BearerAuth, Tools, add_client, build, client_for, revoke_client
from orbit_server.people import People

URL = "http://127.0.0.1:8788/mcp"

CALL_NOTE = """---
type: "call"
created: "2026-11-03T14:30+09:00"
contact: "김민수"
event_id: "e1"
---

# 다음 회의 일정 확정

## 요약
김민수 교수님과 다음 주 화요일 공대 9호관에서 회의하기로 함.

## 사람
- [[김민수]]: 지도교수

## 전사
> [!note]- 전사문 (로컬 STT)
> 실험 데이터는 금요일까지 비밀번호 걸어서 보내 주세요
"""

SHARE_NOTE = """---
type: "share"
created: "2026-11-01T10:00+09:00"
---

# 대학원 AI 세미나

## 요약
소형 GPU에서 음성 인식 경량화 세미나 안내.
"""


@pytest.fixture
def home(tmp_path):
    inbox = tmp_path / "vault" / "00 Inbox"
    inbox.mkdir(parents=True)
    (inbox / "2026-11-03 1430 김민수 다음 회의 일정 확정.md").write_text(CALL_NOTE, encoding="utf-8")
    (inbox / "2026-11-01 1000 대학원 AI 세미나.md").write_text(SHARE_NOTE, encoding="utf-8")
    return tmp_path


def test_index_korean_short_and_long_terms(home):
    idx = Index(home / "data" / "index.db", home / "vault")
    assert idx.refresh() == 2
    assert [h.title for h in idx.search("회의")] == ["다음 회의 일정 확정"]  # 2글자: LIKE
    assert [h.title for h in idx.search("9호관 회의")] == ["다음 회의 일정 확정"]  # 3글자 이상: FTS5
    assert [h.title for h in idx.search("경량화", types=["share"])] == ["대학원 AI 세미나"]
    assert idx.search("세미나", since="2026-11-02") == []
    assert idx.search("없는말") == []


def test_index_excludes_transcript_unless_full(home):
    idx = Index(home / "data" / "index.db", home / "vault")
    assert idx.search("비밀번호") == []
    hit = idx.search("비밀번호", include_transcripts=True)[0]
    assert hit.section == "전사" and "비밀번호" in hit.snippet


def test_index_refresh_tracks_changes_and_deletes(home):
    idx = Index(home / "data" / "index.db", home / "vault")
    idx.refresh()
    note = home / "vault" / "00 Inbox" / "2026-11-01 1000 대학원 AI 세미나.md"
    note.write_text(SHARE_NOTE.replace("경량화", "양자화"), encoding="utf-8")
    os.utime(note, (1, 1))  # mtime을 확실히 바꾼다
    assert idx.refresh() == 1
    assert idx.search("경량화") == [] and idx.search("양자화")
    note.unlink()
    idx.refresh()
    assert idx.search("양자화") == []


def test_strip_sections_hides_transcript():
    out = strip_sections(CALL_NOTE, "전사")
    assert "비밀번호" not in out and "원 녹취를 보여 주지 않습니다" in out and "## 사람" in out


def test_tools_read_note_scope_and_path_guard(home):
    t = Tools(home, home / "vault")
    path = "00 Inbox/2026-11-03 1430 김민수 다음 회의 일정 확정.md"
    assert "비밀번호" not in t.read_note(path)["content"]
    assert "비밀번호" in t.read_note(path, full=True)["content"]
    for bad in ("../data/orbit.db", "/etc/passwd", "00 Inbox/없는.md", "../vault/../x.md"):
        with pytest.raises(ValueError):
            t.read_note(bad)


def test_tools_person_and_events(home):
    t = Tools(home, home / "vault")
    People(t.conn).upsert(name="김민수", source="app", phones=["010-0000-1234"], relation="지도교수",
                          aliases=["교수님"])
    p = t.get_person("교수님")
    assert p["found"] and p["relation"] == "지도교수" and p["notes"][0]["title"] == "다음 회의 일정 확정"
    assert t.get_person("+82 10-0000-1234")["name"] == "김민수"
    assert t.get_person("없는사람") == {"found": False, "candidates": []}

    # worker가 만든 후보 테이블 모양 그대로
    from orbit_server.worker import WORKER_SCHEMA

    t.conn.executescript(WORKER_SCHEMA)
    f = {"title": "연구 회의", "start": "2026-11-10T14:00:00+09:00", "end": "2026-11-10T15:00:00+09:00",
         "all_day": False, "people": ["교수님"]}
    t.conn.execute("INSERT INTO candidates VALUES ('c1', 'e1', 'schedule', ?, '[]', 0.9, 'm', 'primary', NULL)",
                   (json.dumps(f, ensure_ascii=False),))
    t.conn.execute("INSERT INTO candidates VALUES ('c2', 'e1', 'task', ?, '[]', 0.8, 'm', 'primary', NULL)",
                   (json.dumps({"title": "발표 자료 준비"}, ensure_ascii=False),))
    t.conn.execute("INSERT INTO decisions VALUES ('c1', 'auto_register', NULL, ?, 'g1')", (db.now(),))
    ev = t.list_events("2026-11-01", "2026-11-30")
    assert [(e["title"], e["status"]) for e in ev["events"]] == [("연구 회의", "등록됨")]
    assert [x["title"] for x in ev["undated_tasks"]] == ["발표 자료 준비"]
    assert t.list_events("2026-12-01", "2026-12-31")["events"] == []


def test_client_tokens(tmp_path):
    conn = db.connect(tmp_path / "o.db")
    tok = add_client(conn, "claude-code")
    assert client_for(conn, f"Bearer {tok}") == ("claude-code", "summary")
    assert client_for(conn, "Bearer wrong") is None and client_for(conn, tok) is None
    assert revoke_client(conn, "claude-code")
    assert client_for(conn, f"Bearer {tok}") is None
    with pytest.raises(ValueError):
        add_client(conn, "x", scope="admin")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_mcp_over_http_with_auth_and_scope(home):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    tools = Tools(home, home / "vault")
    summary_tok = add_client(tools.conn, "claude-code")
    full_tok = add_client(tools.conn, "local", scope="full")
    mcp = build(tools)
    asgi = BearerAuth(mcp.streamable_http_app(), tools.conn)

    async def call(token, name, args):
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                     transport=httpx.ASGITransport(app=asgi)) as hc:
            async with streamable_http_client(URL, http_client=hc) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    names = {x.name: x for x in (await s.list_tools()).tools}
                    res = await s.call_tool(name, args)
                    return names, json.loads(res.content[0].text)

    async with mcp.session_manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi)) as h:
            assert (await h.post(URL, json={})).status_code == 401

        names, out = await call(summary_tok, "search_notes", {"query": "비밀번호"})
        assert set(names) == {"search_notes", "read_note", "recent_notes", "get_person", "list_people",
                              "list_events"}
        assert all(t.annotations.readOnlyHint for t in names.values())
        assert out["results"] == []  # summary 범위는 원 녹취를 검색하지 않는다

        _, out = await call(full_tok, "search_notes", {"query": "비밀번호"})
        assert out["results"][0]["section"] == "전사"

        path = "00 Inbox/2026-11-03 1430 김민수 다음 회의 일정 확정.md"
        _, out = await call(summary_tok, "read_note", {"path": path})
        assert "비밀번호" not in out["content"] and "9호관" in out["content"]
