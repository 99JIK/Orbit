"""Google Calendar 등록과 되돌리기 (spec 7.1, 10.3).

사용자 본인 GCP 프로젝트의 OAuth 클라이언트와 본인 토큰만 쓴다. 토큰은 `$ORBIT_HOME/secrets/`에 권한 0600으로 둔다.
OS 키체인 연동(spec 4.7)은 아직 없다.

event id는 후보 id에서 정해진다. 같은 이벤트를 다시 처리해도 같은 Google 일정을 갱신할 뿐 중복이 생기지 않는다.
사용자가 캘린더에서 직접 지운 일정(cancelled)은 재처리 때 되살리지 않는다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .candidates import Candidate

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
#: Google event id는 base32hex(a-v, 0-9)만 허용한다. "orbit" 다섯 글자와 16진 후보 id 모두 범위 안이다
ID_PREFIX = "orbit"


def event_id(cand_id: str) -> str:
    return ID_PREFIX + cand_id


def build_body(c: Candidate, cand_id: str, timezone: str, source_note: str) -> dict:
    assert c.when is not None
    desc = [f"Orbit 자동 등록 · 확신도 {c.confidence:.2f}"]
    if c.when.assumed:
        desc.append("추정한 값: " + ", ".join(sorted(c.when.assumed)))
    if source_note:
        desc.append(f"노트: {source_note}")
    body = {
        "summary": c.title,
        "description": "\n".join(desc),
        "extendedProperties": {"private": {"orbit_id": cand_id}},
    }
    if c.when.all_day:
        body["start"] = {"date": c.when.start.date().isoformat()}
        body["end"] = {"date": c.when.end.date().isoformat()}  # 종일 일정 end는 배타적
    else:
        body["start"] = {"dateTime": c.when.start.isoformat(), "timeZone": timezone}
        body["end"] = {"dateTime": c.when.end.isoformat(), "timeZone": timezone}
    return body


@dataclass(frozen=True)
class Written:
    event_id: str
    status: str  # created | updated | dry_run | skipped_deleted
    html_link: str | None


def write_secret(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)  # 기존 파일 권한이 넓었던 경우


def authorize(client_secret: Path, token: Path) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_secret.exists():
        raise FileNotFoundError(f"OAuth 클라이언트 파일 없음: {client_secret}")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    write_secret(token, creds.to_json())


class DryRun:
    dry_run = True

    def __init__(self, reason: str):
        self.reason = reason

    def upsert(self, eid: str, body: dict) -> Written:
        log.info("[dry-run] 일정 %s %s", body["summary"], body["start"])
        return Written(eid, "dry_run", None)

    def delete(self, eid: str) -> None:
        log.info("[dry-run] 삭제 %s", eid)


class GoogleCalendar:
    dry_run = False
    reason = ""

    def __init__(self, token: Path, calendar_id: str = "primary"):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        self.calendar_id = calendar_id
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            write_secret(token, creds.to_json())
        self.svc = build("calendar", "v3", credentials=creds, cache_discovery=False)

    def upsert(self, eid: str, body: dict) -> Written:
        from googleapiclient.errors import HttpError

        events = self.svc.events()
        try:
            r = events.insert(calendarId=self.calendar_id, body={**body, "id": eid}).execute()
            return Written(eid, "created", r.get("htmlLink"))
        except HttpError as e:
            if e.resp.status != 409:
                raise
        current = events.get(calendarId=self.calendar_id, eventId=eid).execute()
        if current.get("status") == "cancelled":
            return Written(eid, "skipped_deleted", None)
        r = events.patch(calendarId=self.calendar_id, eventId=eid, body=body).execute()
        return Written(eid, "updated", r.get("htmlLink"))

    def delete(self, eid: str) -> None:
        from googleapiclient.errors import HttpError

        try:
            self.svc.events().delete(calendarId=self.calendar_id, eventId=eid).execute()
        except HttpError as e:
            if e.resp.status not in (404, 410):  # 이미 지워짐
                raise


def make_calendar(enabled: bool, token: Path, calendar_id: str):
    if not enabled:
        return DryRun("calendar.enabled = false")
    if not token.exists():
        return DryRun(f"토큰 없음, `orbit-server auth google` 필요 ({token})")
    return GoogleCalendar(token, calendar_id)
