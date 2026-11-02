"""명령줄 진입점.

    orbit-server serve [--host 0.0.0.0] [--port 8787]
    orbit-server device add <device_id>
    orbit-server device list
    orbit-server device revoke <device_id>
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from . import db
from .app import create_app, default_home


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orbit-server")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="수집 API 실행")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8787)

    device = sub.add_parser("device", help="기기 토큰 관리")
    device_sub = device.add_subparsers(dest="action", required=True)
    device_sub.add_parser("add").add_argument("device_id")
    device_sub.add_parser("list")
    device_sub.add_parser("revoke").add_argument("device_id")

    args = parser.parse_args(argv)
    home = default_home()

    if args.command == "serve":
        import uvicorn

        uvicorn.run(create_app(home), host=args.host, port=args.port)
        return 0

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


if __name__ == "__main__":
    sys.exit(main())
