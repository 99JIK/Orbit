"""명령줄 진입점.

    orbit-server serve [--host 0.0.0.0] [--port 8787]
    orbit-server device add <device_id>
    orbit-server device list
    orbit-server device revoke <device_id>
    orbit-server worker [--once]
    orbit-server add <파일...> [--call 상대이름] [--at ISO시각]
    orbit-server reprocess <event_id>
    orbit-server undo <후보 id 앞자리 | event_id>
    orbit-server status [-n 20]
    orbit-server auth google
    orbit-server people
    orbit-server mcp [--host 127.0.0.1] [--port 8788]
    orbit-server mcp-client add <이름> [--scope summary|full]
    orbit-server mcp-client list
    orbit-server mcp-client revoke <이름>
    orbit-server reindex
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .app import create_app, default_home

AUDIO_EXTS = {".m4a", ".mp3", ".ogg", ".wav", ".amr", ".3gp", ".aac", ".flac", ".opus"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orbit-server")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="수집 API 실행")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8787)

    device = sub.add_parser("device", help="기기 토큰 관리")
    device_sub = device.add_subparsers(dest="action", required=True)
    device_sub.add_parser("add").add_argument("device_id")
    device_sub.add_parser("list")
    device_sub.add_parser("revoke").add_argument("device_id")

    worker = sub.add_parser("worker", help="큐 처리 (STT, 요약·추출, 등록, 볼트, 알림)")
    worker.add_argument("--once", action="store_true", help="쌓인 것만 처리하고 종료")

    add = sub.add_parser("add", help="이 PC의 파일을 큐에 넣는다 (spec 7.3 데스크톱 입력)")
    add.add_argument("paths", nargs="+")
    add.add_argument("--call", metavar="NAME", help="통화 녹음으로 넣고 상대 이름을 붙인다")
    add.add_argument("--at", help="캡처 시각(ISO, 기본: 파일 수정 시각)")

    sub.add_parser("reprocess", help="요약·추출부터 다시").add_argument("event_id")
    sub.add_parser("undo", help="자동 등록한 일정 되돌리기").add_argument("id")
    st = sub.add_parser("status", help="최근 이벤트 처리 상태")
    st.add_argument("-n", type=int, default=20)
    sub.add_parser("auth", help="외부 서비스 로그인").add_argument("provider", choices=["google"])
    sub.add_parser("people", help="사람 사전 보기 (앱 관계 메모, 명함, config/context.yaml)")
    mcp = sub.add_parser("mcp", help="외부 AI용 MCP 서버 (읽기 전용, docs/mcp.md)")
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8788)
    mc = sub.add_parser("mcp-client", help="MCP 클라이언트 토큰 관리").add_subparsers(dest="action", required=True)
    mc_add = mc.add_parser("add")
    mc_add.add_argument("name")
    mc_add.add_argument("--scope", choices=["summary", "full"], default="summary",
                        help="summary: 원 녹취 제외(기본), full: 원 녹취 포함")
    mc.add_parser("list")
    mc.add_parser("revoke").add_argument("name")
    sub.add_parser("reindex", help="검색 인덱스 다시 만들기 (data/index.db)")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "huggingface_hub", "googleapiclient.discovery_cache", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    home = default_home()

    if args.command == "serve":
        import uvicorn

        uvicorn.run(create_app(home), host=args.host, port=args.port)
        return 0
    if args.command == "device":
        return _device(home, args)
    if args.command == "worker":
        from .worker import Worker

        w = Worker(home)
        if args.once:
            print(f"처리 {w.tick()}건")
        else:
            w.run()
        return 0
    if args.command == "add":
        return _add(home, args)
    if args.command == "reprocess":
        from .worker import Worker

        note = Worker(home).reprocess(args.event_id)
        print(f"완료: {note}")
        return 0
    if args.command == "undo":
        from .worker import Worker

        done = Worker(home).undo(args.id)
        for d in done:
            print(f"삭제: {d}")
        if not done:
            print("되돌릴 일정 없음 (id가 틀렸거나 이미 되돌림)", file=sys.stderr)
            return 1
        return 0
    if args.command == "status":
        return _status(home, args.n)
    if args.command in ("mcp", "mcp-client", "reindex"):
        return _mcp(home, args)
    if args.command == "people":
        from .people import People

        for p in People(db.connect(home / "data" / "orbit.db"), home / "config" / "context.yaml").all():
            print(f"{p.describe()}  [{', '.join(p.phones)}]")
        return 0
    if args.command == "auth":
        from .gcal import authorize

        secrets = home / "secrets"
        authorize(secrets / "google-client.json", secrets / "google-token.json")
        print(f"토큰 저장(권한 0600): {secrets / 'google-token.json'}")
        print("config/worker.yaml 에 calendar_enabled: true 를 넣으면 실제 등록을 시작합니다")
        return 0
    return 0


def _vault(home: Path) -> Path:
    from .worker import Settings

    return Settings.load(home).vault_dir or home / "vault"


def _mcp(home: Path, args) -> int:
    from . import mcp_server

    if args.command == "mcp":
        mcp_server.serve(home, _vault(home), args.host, args.port)
        return 0
    if args.command == "reindex":
        from .index import Index

        (home / "data" / "index.db").unlink(missing_ok=True)
        print(f"색인한 노트 {Index(home / 'data' / 'index.db', _vault(home)).refresh()}개")
        return 0
    conn = db.connect(home / "data" / "orbit.db")
    if args.action == "add":
        try:
            token = mcp_server.add_client(conn, args.name, args.scope)
        except sqlite3.IntegrityError:
            print(f"이미 있는 이름입니다: {args.name}", file=sys.stderr)
            return 1
        print(f"클라이언트 {args.name} 등록 (범위 {args.scope}). 아래 토큰을 AI 도구 설정에 넣으세요 (다시 표시되지 않음):")
        print(token)
    elif args.action == "list":
        for r in mcp_server.list_clients(conn):
            state = f"폐기 {r['revoked_at']}" if r["revoked_at"] else "활성"
            print(f"{r['name']}\t{r['scope']}\t{r['created_at']}\t{state}")
    elif args.action == "revoke":
        if not mcp_server.revoke_client(conn, args.name):
            print(f"활성 클라이언트가 아닙니다: {args.name}", file=sys.stderr)
            return 1
        print(f"클라이언트 {args.name} 토큰 폐기")
    return 0


def _device(home: Path, args) -> int:
    conn = db.connect(home / "data" / "orbit.db")
    if args.action == "add":
        try:
            token = db.add_device(conn, args.device_id)
        except sqlite3.IntegrityError:
            print(f"이미 등록된 기기입니다: {args.device_id}", file=sys.stderr)
            return 1
        print(f"기기 {args.device_id} 등록 완료. 아래 토큰을 Companion 앱에 입력하세요 (다시 표시되지 않음):")
        print(token)
    elif args.action == "list":
        for row in db.list_devices(conn):
            state = f"폐기 {row['revoked_at']}" if row["revoked_at"] else "활성"
            print(f"{row['device_id']}\t{row['created_at']}\t{state}")
    elif args.action == "revoke":
        if not db.revoke_device(conn, args.device_id):
            print(f"활성 상태인 기기가 아닙니다: {args.device_id}", file=sys.stderr)
            return 1
        print(f"기기 {args.device_id} 토큰 폐기 완료")
    return 0


def _add(home: Path, args) -> int:
    """`/ingest/file`과 같은 모양의 이벤트를 만든다. dedupe_key가 파일 해시라 같은 파일은 한 번만 들어간다."""
    conn = db.connect(home / "data" / "orbit.db")
    for p in map(Path, args.paths):
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        key = f"desktop:{sha}"
        if dup := db.find_duplicate(conn, "", key):
            print(f"이미 있음: {p} (event {dup})")
            continue
        captured = (datetime.fromisoformat(args.at) if args.at
                    else datetime.fromtimestamp(p.stat().st_mtime).astimezone())
        if captured.tzinfo is None:
            captured = captured.astimezone()
        is_audio = p.suffix.lower() in AUDIO_EXTS
        event_id = str(uuid.uuid4())
        rel = Path("raw") / f"{captured.astimezone(timezone.utc):%Y/%m}" / f"{event_id}{p.suffix.lower()}"
        (home / "data" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, home / "data" / rel)
        payload: dict = {"file_name": p.name}
        if is_audio:
            payload["origin"] = "call" if args.call else "voice"
            if args.call:
                payload["call"] = {"name": args.call}
        db.insert_event(conn, {
            "event_id": event_id, "source": "desktop", "app": "orbit-server add",
            "type": "audio" if is_audio else "share", "captured_at": captured.isoformat(),
            "received_at": db.now(), "device_id": "desktop", "payload": json.dumps(payload, ensure_ascii=False),
            "text": None, "file_ref": rel.as_posix(), "dedupe_key": key,
        })
        print(f"큐에 넣음: {p} -> event {event_id}")
    return 0


def _status(home: Path, n: int) -> int:
    conn = db.connect(home / "data" / "orbit.db")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    err = "error" if "error" in cols else "NULL AS error"
    for r in conn.execute(f"SELECT event_id, type, status, captured_at, {err} FROM events "
                          "ORDER BY received_at DESC LIMIT ?", (n,)):
        tail = f"  ! {r['error'][:120]}" if r["error"] else ""
        print(f"{r['status']:10} {r['type']:6} {r['captured_at'][:16]} {r['event_id']}{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
