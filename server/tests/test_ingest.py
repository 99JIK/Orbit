import json
import uuid

import pytest
from fastapi.testclient import TestClient

from orbit_server import db
from orbit_server.app import create_app


@pytest.fixture
def server(tmp_path):
    app = create_app(tmp_path)
    token = db.add_device(app.state.conn, "galaxy-01")
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {token}"
    return client, app, tmp_path


def make_event(**overrides):
    event = {
        "event_id": str(uuid.uuid4()),
        "source": "phone",
        "type": "share",
        "captured_at": "2026-11-02T15:24:00+09:00",
        "payload": {"text": "내일 오후 3시 교수님 면담"},
        "dedupe_key": uuid.uuid4().hex,
        "device_id": "galaxy-01",
    }
    event.update(overrides)
    return event


def test_accepts_event_and_uses_token_device(server):
    client, app, _ = server
    event = make_event(device_id="spoofed")
    res = client.post("/ingest", json=event)
    assert res.status_code == 201
    assert res.json() == {"status": "accepted", "event_id": event["event_id"]}
    row = app.state.conn.execute("SELECT * FROM events").fetchone()
    assert row["device_id"] == "galaxy-01"
    assert row["text"] == "내일 오후 3시 교수님 면담"
    assert row["status"] == "queued"


def test_duplicate_event_id_and_dedupe_key(server):
    client, _, _ = server
    event = make_event()
    assert client.post("/ingest", json=event).status_code == 201
    retry = client.post("/ingest", json=event)
    assert retry.status_code == 200 and retry.json()["status"] == "duplicate"
    same_content = client.post("/ingest", json=make_event(dedupe_key=event["dedupe_key"]))
    assert same_content.json() == {"status": "duplicate", "event_id": event["event_id"]}


def test_rejects_naive_captured_at(server):
    client, _, _ = server
    assert client.post("/ingest", json=make_event(captured_at="2026-11-02T15:24:00")).status_code == 422


def test_rejects_missing_and_revoked_token(server):
    client, app, _ = server
    assert client.post("/ingest", json=make_event(), headers={"Authorization": ""}).status_code == 401
    db.revoke_device(app.state.conn, "galaxy-01")
    assert client.post("/ingest", json=make_event()).status_code == 401


def test_file_upload_is_stored_under_raw(server):
    client, app, home = server
    event = make_event(type="audio", payload={"origin": "call", "file_name": "call.m4a"})
    res = client.post(
        "/ingest/file",
        data={"event": json.dumps(event)},
        files={"file": ("call.m4a", b"fake-audio", "audio/mp4")},
    )
    assert res.status_code == 201
    file_ref = app.state.conn.execute("SELECT file_ref FROM events").fetchone()["file_ref"]
    assert file_ref == f"raw/2026/11/{event['event_id']}.m4a"
    assert (home / "data" / file_ref).read_bytes() == b"fake-audio"


def test_file_upload_rejects_bad_event_json(server):
    client, _, _ = server
    res = client.post("/ingest/file", data={"event": "{}"}, files={"file": ("a.m4a", b"x", "audio/mp4")})
    assert res.status_code == 422


def test_heartbeat_and_health(server):
    client, app, _ = server
    res = client.post("/heartbeat", json={"app_version": "0.1.0", "listener_ok": True, "queue_len": 2})
    assert res.status_code == 204
    beat = app.state.conn.execute("SELECT * FROM heartbeats").fetchone()
    assert (beat["device_id"], beat["listener_ok"], beat["queue_len"]) == ("galaxy-01", 1, 2)

    health = client.get("/health").json()
    assert health["status"] == "ok" and health["device_id"] == "galaxy-01"
    anonymous = client.get("/health", headers={"Authorization": ""}).json()
    assert "device_id" not in anonymous
