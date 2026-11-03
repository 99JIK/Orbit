import json

import pytest

from orbit_server import db
from orbit_server.people import People, normalize_phone

from test_worker import CALL_DIGEST, add_event, make  # noqa: F401  (fixture 재사용)

# 합성 데이터. 실존 인물·번호 아님(010-0000-xxxx는 할당되지 않는 대역)
NUM = "010-0000-1234"


@pytest.mark.parametrize("raw,norm", [
    ("010-0000-1234", "01000001234"), ("+82 10-0000-1234", "01000001234"), ("(02) 000-0000", "020000000"), ("", ""),
])
def test_normalize_phone(raw, norm):
    assert normalize_phone(raw) == norm


def test_upsert_by_phone_and_rename_keeps_alias(tmp_path):
    p = People(db.connect(tmp_path / "x.db"))
    a, new = p.upsert(name="김민수", source="app", phones=[NUM], relation="지도교수")
    assert new and a.relation == "지도교수"
    b, new = p.upsert(name="김민수 교수", source="app", phones=["+821000001234"], note="화요일 면담")
    assert not new and b.person_id == a.person_id
    assert b.name == "김민수 교수" and "김민수" in b.aliases and b.relation == "지도교수" and b.note == "화요일 면담"
    c, _ = p.upsert(name="김민수 교수", source="app", phones=[NUM], relation="")
    assert c.relation == ""  # 앱에서 지우면 지운다
    assert p.by_phone("01000001234").name == "김민수 교수"
    assert p.canonical("김민수") == "김민수 교수"


def test_context_yaml_wins_and_merges(tmp_path):
    ctx = tmp_path / "context.yaml"
    ctx.write_text("people:\n  - name: 김민수\n    aliases: [교수님]\n    role: advisor\n", encoding="utf-8")
    p = People(db.connect(tmp_path / "x.db"), ctx)
    p.upsert(name="김민수", source="app", phones=[NUM], note="메모")
    [m] = p.all()
    assert m.relation == "advisor" and m.phones == [NUM] and m.note == "메모" and "교수님" in m.aliases
    assert p.by_phone(NUM).name == "김민수" and p.canonical("교수님") == "김민수"


def test_contact_event_creates_person_note(make, tmp_path):
    w = make()
    eid = add_event(w.conn, tmp_path, type_="contact", data=None,
                    payload={"number": NUM, "name": "김민수", "relation": "지도교수", "note": "연구실 301호"})
    assert w.tick() == 1
    assert w.conn.execute("SELECT status FROM events WHERE event_id=?", (eid,)).fetchone()[0] == "processed"
    note = (tmp_path / "vault" / "00 Inbox" / "김민수.md").read_text(encoding="utf-8")
    assert 'type: "person"' in note and "지도교수" in note and "연구실 301호" in note
    assert w.router.calls == []  # 모델 안 부름


def test_call_uses_registered_person(make, tmp_path):
    w = make(CALL_DIGEST)
    w.people.upsert(name="김민수", source="app", phones=[NUM], relation="지도교수", aliases=["교수님"])
    add_event(w.conn, tmp_path, payload={"origin": "call", "call": {"number": "+821000001234", "name": "캐시이름"}})
    w.tick()
    assert w._transcriber.hints == ["등장인물과 용어: 김민수, 교수님."]
    system = w.router.calls[0]["messages"][0]["content"]
    assert "사용자가 등록한 통화 상대 정보: 김민수 (지도교수)" in system
    note = next(p for p in (tmp_path / "vault" / "00 Inbox").glob("2026-*.md")).read_text(encoding="utf-8")
    assert 'contact: "김민수"' in note


def test_business_card_share(make, tmp_path):
    card = {
        "title": "명함: 박서연 (합성대학교)", "summary": "명함", "key_points": [], "people": [], "tags": ["명함"],
        "items": [],
        "card": {"is_card": True, "name": "박서연", "org": "합성대학교", "title": "연구교수",
                 "phones": ["010-0000-9876"], "emails": ["park@example.com"], "address": ""},
    }
    w = make(card)
    add_event(w.conn, tmp_path, type_="share", payload={"file_name": "card.jpg"}, file_name="card.jpg",
              data=b"\xff\xd8 fake jpeg")
    w.tick()
    p = w.people.by_phone("01000009876")
    assert p and p.org == "합성대학교" and p.emails == ["park@example.com"]
    inbox = tmp_path / "vault" / "00 Inbox"
    assert (inbox / "박서연.md").exists()
    share_note = next(x for x in inbox.glob("2026-*.md")).read_text(encoding="utf-8")
    assert "소속: 합성대학교" in share_note and "[[박서연]]" in share_note


def test_person_note_not_overwritten(make, tmp_path):
    moved = tmp_path / "vault" / "People"
    moved.mkdir(parents=True)
    (moved / "김민수.md").write_text("사용자가 고친 노트", encoding="utf-8")
    w = make()
    add_event(w.conn, tmp_path, type_="contact", data=None, payload={"number": NUM, "name": "김민수", "relation": "x"})
    w.tick()
    assert (moved / "김민수.md").read_text(encoding="utf-8") == "사용자가 고친 노트"
    assert not (tmp_path / "vault" / "00 Inbox" / "김민수.md").exists()


def test_contact_event_accepted_by_api(tmp_path):
    from fastapi.testclient import TestClient

    from orbit_server.app import create_app

    app = create_app(tmp_path)
    token = db.add_device(app.state.conn, "galaxy-01")
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    r = c.post("/ingest", json={
        "event_id": "00000000-0000-4000-8000-000000000001", "source": "phone", "type": "contact",
        "captured_at": "2026-11-03T20:00:00+09:00", "payload": {"number": NUM, "relation": "지도교수"},
        "dedupe_key": "contact-1"})
    assert r.status_code == 201
    assert json.loads(app.state.conn.execute("SELECT payload FROM events").fetchone()[0])["number"] == NUM
