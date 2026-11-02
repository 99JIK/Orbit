"""수집 API (`ingest` 프로세스). 계약은 docs/ingest-api.md를 따른다."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError, field_validator

from . import db

MAX_FILE_BYTES = 500 * 1024 * 1024


class Event(BaseModel):
    event_id: uuid.UUID
    source: Literal["phone", "gmail", "github", "rss", "desktop", "discord"]
    app: str | None = None
    type: Literal["audio", "notification", "sms", "share", "email", "location"]
    captured_at: datetime
    payload: dict[str, Any]
    dedupe_key: str
    device_id: str | None = None

    @field_validator("captured_at")
    @classmethod
    def require_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("captured_at must include a UTC offset")
        return value


class Heartbeat(BaseModel):
    app_version: str | None = None
    listener_ok: bool
    queue_len: int
    device_id: str | None = None
    at: datetime | None = None


def default_home() -> Path:
    return Path(os.environ.get("ORBIT_HOME", Path.home() / "orbit"))


def create_app(home: Path | None = None) -> FastAPI:
    home = home or default_home()
    raw_dir = home / "data" / "raw"
    conn = db.connect(home / "data" / "orbit.db")

    app = FastAPI(title="Orbit ingest", version="0.1.0")
    app.state.conn = conn
    app.state.home = home

    def device(authorization: str | None = Header(default=None)) -> str:
        device_id = _device_from_header(conn, authorization)
        if device_id is None:
            raise HTTPException(status_code=401, detail="invalid or revoked token")
        return device_id

    @app.post("/ingest")
    def ingest(event: Event, device_id: str = Depends(device)) -> JSONResponse:
        if dup := db.find_duplicate(conn, str(event.event_id), event.dedupe_key):
            return _duplicate(dup)
        _store(conn, event, device_id, file_ref=None)
        return _accepted(event)

    @app.post("/ingest/file")
    def ingest_file(
        event: str = Form(...),
        file: UploadFile = File(...),
        device_id: str = Depends(device),
    ) -> JSONResponse:
        try:
            parsed = Event.model_validate_json(event)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=json.loads(e.json()))
        if dup := db.find_duplicate(conn, str(parsed.event_id), parsed.dedupe_key):
            return _duplicate(dup)
        file_ref = _save_file(raw_dir, parsed, file)
        _store(conn, parsed, device_id, file_ref=file_ref)
        return _accepted(parsed)

    @app.post("/heartbeat", status_code=204)
    def heartbeat(beat: Heartbeat, device_id: str = Depends(device)) -> None:
        db.insert_heartbeat(conn, device_id, beat.app_version, beat.listener_ok, beat.queue_len)

    @app.get("/health")
    def health(request: Request) -> dict:
        body: dict[str, Any] = {"status": "ok", **db.stats(conn), "disk_free_gb": _disk_free_gb(home)}
        device_id = _device_from_header(conn, request.headers.get("authorization"))
        if device_id:
            body["device_id"] = device_id
        return body

    return app


def _device_from_header(conn: sqlite3.Connection, authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return db.device_for_token(conn, authorization[7:].strip())


def _store(conn: sqlite3.Connection, event: Event, device_id: str, file_ref: str | None) -> None:
    # 기기 ID는 토큰이 정한다. 본문의 device_id는 참고값일 뿐 신뢰하지 않는다.
    db.insert_event(conn, {
        "event_id": str(event.event_id),
        "source": event.source,
        "app": event.app,
        "type": event.type,
        "captured_at": event.captured_at.isoformat(),
        "received_at": db.now(),
        "device_id": device_id,
        "payload": json.dumps(event.payload, ensure_ascii=False),
        "text": event.payload.get("text"),
        "file_ref": file_ref,
        "dedupe_key": event.dedupe_key,
    })


def _save_file(raw_dir: Path, event: Event, upload: UploadFile) -> str:
    """원본을 `raw/YYYY/MM/<event_id><ext>`에 저장하고 data/ 기준 상대 경로를 돌려준다."""
    captured = event.captured_at.astimezone(timezone.utc)
    relative = Path("raw") / f"{captured:%Y}" / f"{captured:%m}" / f"{event.event_id}{_safe_suffix(upload.filename)}"
    target = raw_dir.parent / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    written = 0
    with partial.open("wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_FILE_BYTES:
                out.close()
                partial.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="file too large")
            out.write(chunk)
    partial.replace(target)
    return relative.as_posix()


def _safe_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ""


def _disk_free_gb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return round(shutil.disk_usage(path).free / 1024**3, 1)


def _accepted(event: Event) -> JSONResponse:
    return JSONResponse({"status": "accepted", "event_id": str(event.event_id)}, status_code=201)


def _duplicate(existing_id: str) -> JSONResponse:
    return JSONResponse({"status": "duplicate", "event_id": existing_id}, status_code=200)
