"""worker 프로세스 (spec 4.1, 5장). 큐에 쌓인 이벤트를 STT → 요약·추출 → 정규화 → 등록 → 볼트 → 알림 순서로 처리한다.

지금 처리하는 이벤트는 `type`이 `audio`(통화·음성 녹음)와 `share`(공유 텍스트·파일)인 것이다. 나머지 유형은 큐에 둔다.

일정 등록은 승인을 기다리지 않는다(2026-11-03 사용자 결정, spec 6장 갱신). 확신도가 기준 이상이고 날짜가 해석된
일정·마감만 바로 Calendar에 넣고, 검토 알림을 한 번 보낸다. 확신도 기준은 spec 5.1의 0.8을 그대로 쓴다.

비싼 단계 결과는 DB에 남긴다. 전사문은 `events.text`, 요약·추출은 `digests`. 뒤 단계가 실패해 재시도할 때
STT와 모델을 다시 부르지 않고, 모델이 매번 다른 항목을 뽑아 일정이 중복되는 일도 막는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import content, db, digest, gcal, llm, notify
from .people import People
from .candidates import Candidate
from .vault import Vault

log = logging.getLogger(__name__)

HANDLED_TYPES = ("audio", "share", "contact")
WORKER_SCHEMA = """
CREATE TABLE IF NOT EXISTS digests (
    event_id    TEXT PRIMARY KEY,
    body        TEXT NOT NULL,
    model       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    cand_id     TEXT PRIMARY KEY,
    event_id    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    fields      TEXT NOT NULL,
    assumed     TEXT NOT NULL,
    confidence  REAL NOT NULL,
    model       TEXT NOT NULL,
    calendar    TEXT,
    conflict    TEXT
);
CREATE INDEX IF NOT EXISTS candidates_event ON candidates (event_id);
CREATE TABLE IF NOT EXISTS decisions (
    cand_id       TEXT NOT NULL,
    action        TEXT NOT NULL,
    edited_fields TEXT,
    decided_at    TEXT NOT NULL,
    google_id     TEXT
);
CREATE INDEX IF NOT EXISTS decisions_cand ON decisions (cand_id);
"""


@dataclass
class Settings:
    timezone: str = "Asia/Seoul"
    # STT
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "auto"
    whisper_compute: str = "auto"
    unload_after_idle_s: int = 300
    # 캘린더
    calendar_enabled: bool = False
    calendar_id: str = "primary"
    min_confidence: float = 0.8
    calendar_from_share: bool = False
    # 알림
    notify: str = "stdout"  # stdout | discord_webhook
    # 볼트
    vault_dir: Path | None = None
    include_transcript: bool = True
    # 재시도
    poll_s: float = 5.0
    max_attempts: int = 3
    retry_after_s: int = 600
    llm: dict = field(default_factory=dict)

    @classmethod
    def load(cls, home: Path) -> Settings:
        s = cls()
        cfg = home / "config"
        for name in ("worker.yaml", "llm.yaml"):
            if not (cfg / name).exists():
                continue
            import yaml

            data = yaml.safe_load((cfg / name).read_text(encoding="utf-8")) or {}
            if name == "llm.yaml":
                s.llm = data
                continue
            for k, v in data.items():
                if not hasattr(s, k):
                    raise ValueError(f"config/worker.yaml: 알 수 없는 키 {k}")
                setattr(s, k, Path(os.path.expanduser(v)) if k == "vault_dir" and v else v)
        return s


def cand_id(event_id: str, idx: int, c: Candidate) -> str:
    return hashlib.sha1(f"{event_id}:{idx}:{c.kind}:{c.title}".encode()).hexdigest()


def _when_text(c: Candidate, tz: ZoneInfo) -> str:
    if c.when is None:
        return ""
    s = c.when.start.astimezone(tz)
    if c.when.all_day:
        return f"{s:%Y-%m-%d} (종일)"
    return f"{s:%Y-%m-%d %H:%M}-{c.when.end.astimezone(tz):%H:%M}"


class Worker:
    def __init__(self, home: Path, settings: Settings | None = None, *, router=None, transcriber=None,
                 calendar=None, notifier=None, fetch=None):
        self.home = home
        self.s = settings or Settings.load(home)
        self.tz = ZoneInfo(self.s.timezone)
        self.conn = db.connect(home / "data" / "orbit.db")
        self.conn.executescript(WORKER_SCHEMA)
        self._migrate()
        self.data = home / "data"
        self.secrets = home / "secrets"
        self.vault = Vault(self.s.vault_dir or home / "vault")
        self.router = router or llm.build_router(self.s.llm or None, self.conn)
        self._transcriber = transcriber
        self._last_stt = 0.0
        self.calendar = calendar or gcal.make_calendar(
            self.s.calendar_enabled, self.secrets / "google-token.json", self.s.calendar_id)
        self.notifier = notifier or self._make_notifier()
        self.fetch = fetch or content.fetch_url
        self.people = People(self.conn, home / "config" / "context.yaml")
        self._stop = False

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(events)")}
        if "attempts" not in cols:
            self.conn.execute("ALTER TABLE events ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        if "error" not in cols:
            self.conn.execute("ALTER TABLE events ADD COLUMN error TEXT")
        if "updated_at" not in cols:
            self.conn.execute("ALTER TABLE events ADD COLUMN updated_at TEXT")
        # 처리 중 끊긴 이벤트는 실패로 돌려 재시도 대상에 넣는다
        self.conn.execute("UPDATE events SET status='failed', error='중단됨' WHERE status='processing'")

    def _make_notifier(self):
        if self.s.notify == "discord_webhook":
            url = os.environ.get("ORBIT_DISCORD_WEBHOOK_URL", "")
            f = self.secrets / "discord-webhook.txt"
            if not url and f.exists():
                url = f.read_text(encoding="utf-8").strip()
            return notify.DiscordWebhook(url)
        return notify.StdoutNotifier()

    # ── 큐 ────────────────────────────────────────────────────────────────

    def next_events(self) -> list[sqlite3.Row]:
        q = ",".join("?" for _ in HANDLED_TYPES)
        # db.now()와 같은 UTC ISO 형식이어야 문자열 비교가 맞다
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=self.s.retry_after_s)).isoformat(timespec="seconds")
        return self.conn.execute(
            f"""SELECT * FROM events WHERE type IN ({q}) AND (
                    status = 'queued'
                    OR (status = 'failed' AND attempts < ? AND (updated_at IS NULL OR updated_at <= ?)))
                ORDER BY received_at""",
            (*HANDLED_TYPES, self.s.max_attempts, cutoff),
        ).fetchall()

    def tick(self) -> int:
        n = 0
        for ev in self.next_events():
            if self._stop:
                break
            try:
                self.process(ev["event_id"])
                n += 1
            except Exception:
                log.exception("처리 실패: %s", ev["event_id"])
        self._maybe_unload()
        return n

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "_stop", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "_stop", True))
        log.info("worker 시작: %s", self.home)
        while not self._stop:
            self.tick()
            for _ in range(int(self.s.poll_s * 10)):
                if self._stop:
                    break
                time.sleep(0.1)

    def reprocess(self, event_id: str) -> Path:
        """요약·추출부터 다시 한다. 전사문은 같은 오디오면 같으므로 유지한다."""
        self.conn.execute("DELETE FROM digests WHERE event_id = ?", (event_id,))
        self.conn.execute("UPDATE events SET status='queued', attempts=0, error=NULL WHERE event_id=?", (event_id,))
        return self.process(event_id)

    # ── 한 건 처리 ──────────────────────────────────────────────────────

    def process(self, event_id: str) -> Path:
        ev = self.conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        if ev is None:
            raise KeyError(event_id)
        self.conn.execute(
            "UPDATE events SET status='processing', attempts=attempts+1, updated_at=? WHERE event_id=?",
            (db.now(), event_id))
        try:
            note = self._process(ev)
        except Exception as e:
            self.conn.execute("UPDATE events SET status='failed', error=?, updated_at=? WHERE event_id=?",
                              (f"{type(e).__name__}: {e}"[:2000], db.now(), event_id))
            raise
        self.conn.execute("UPDATE events SET status='processed', error=NULL, updated_at=? WHERE event_id=?",
                          (db.now(), event_id))
        return note

    def _process(self, ev: sqlite3.Row) -> Path:
        payload = json.loads(ev["payload"])
        if ev["type"] == "contact":
            return self._contact(payload)
        captured = datetime.fromisoformat(ev["captured_at"]).astimezone(self.tz)
        call = payload.get("call") or {}
        # 사용자가 앱에 등록한 사람이면 그 이름이 통화 기록의 캐시 이름보다 우선한다
        person = self.people.by_phone(call.get("number"))
        contact = person.name if person else (call.get("name") or "")
        origin = payload.get("origin") or ("call" if call else "voice")
        file_path = self.data / ev["file_ref"] if ev["file_ref"] else None

        transcript = extracted = None
        images: list[Path] = []
        source_url = ""
        original = None
        if ev["type"] == "audio":
            source = origin if origin in ("call", "voice") else "voice"
            hint = person.hint_names() if person else [contact]
            transcript = ev["text"] or self._transcribe(ev["event_id"], file_path, hint)
            text = transcript
        else:
            source = "share"
            if file_path is not None:
                ex = content.from_file(file_path, self.fetch)
                original = file_path
            else:
                ex = content.from_text(payload.get("text") or "", self.fetch)
            text, images, source_url = ex.text, ex.images, ex.source_url
            if payload.get("title"):
                text = f"제목: {payload['title']}\n\n{text}"
            extracted = text if file_path is not None or source_url else None
        if not text.strip():
            raise ValueError("텍스트 비어 있음(무음이거나 내용 없음)")

        d = self._digest(ev["event_id"], text, source, captured, contact, images,
                         person.describe() if person else None)
        cands = digest.candidates(d, captured)
        people = [{"name": self.people.canonical(x["name"]), "note": x["note"]} for x in d.people]
        card_md: list[str] = []
        if d.card and source == "share":
            card_person, _ = self.people.upsert(
                name=d.card["name"], source="card", phones=d.card["phones"], emails=d.card["emails"],
                org=d.card["org"], title=d.card["title"])
            self.vault.write_person(card_person)
            people = [{"name": card_person.name, "note": "명함"}]
            card_md = _card_md(d.card)

        lines, items_md = [], []
        for i, c in enumerate(cands):
            cid = cand_id(ev["event_id"], i, c)
            line = self._register(cid, ev["event_id"], c, d.model, source, d.title)
            lines.append(line)
            mark = "- [ ]" if c.kind in ("task", "deadline") else "-"
            when = f" {line.when}" if line.when else ""
            assumed = " (추정)" if c.when and c.when.assumed else ""
            items_md.append(f"{mark} {notify.KIND_LABEL.get(c.kind, c.kind)}{when} **{c.title}**{assumed}")

        note = self.vault.write(
            event_id=ev["event_id"], source=source, captured_at=captured, title=d.title, summary=d.summary,
            key_points=[*card_md, *d.key_points], people=people, tags=d.tags, items=items_md,
            transcript=transcript if self.s.include_transcript else None, extracted=extracted,
            original=original, source_url=source_url, contact=contact,
        )
        try:
            self.notifier.send(notify.format_review(source, d.title, self.vault.rel(note), lines,
                                                    getattr(self.calendar, "reason", "")))
        except Exception:
            # 알림 실패로 처리 전체를 실패시키지 않는다. 일정은 이미 들어갔고, 재처리하면 알림만 다시 간다
            log.exception("알림 전송 실패")
        return note

    def _contact(self, payload: dict) -> Path:
        """앱에서 번호에 관계·메모를 붙인 이벤트. 모델을 부르지 않는다."""
        number = str(payload.get("number") or "").strip()
        if not number:
            raise ValueError("contact 이벤트에 number 없음")
        person, _ = self.people.upsert(
            name=str(payload.get("name") or "").strip() or number, source="app", phones=[number],
            relation=payload.get("relation"), note=payload.get("note"))
        return self.vault.write_person(person)

    def _transcribe(self, event_id: str, path: Path | None, names: list[str]) -> str:
        from .stt import FasterWhisper, build_hint

        if path is None or not path.exists():
            raise FileNotFoundError(f"녹음 파일 없음: {path}")
        if self._transcriber is None:
            self._transcriber = FasterWhisper(self.s.whisper_model, self.s.whisper_device, self.s.whisper_compute)
        t = self._transcriber.transcribe(path, build_hint(names))
        self._last_stt = time.time()
        log.info("STT %ss 오디오, %ss 걸림", t.audio_s, t.latency_s)
        self.conn.execute("UPDATE events SET text = ? WHERE event_id = ?", (t.text, event_id))
        return t.text

    def _maybe_unload(self) -> None:
        idle = self.s.unload_after_idle_s
        if self._transcriber is not None and self._last_stt and time.time() - self._last_stt > idle:
            self._transcriber.unload()
            self._last_stt = 0.0
            log.info("STT 모델 내림 (유휴 %ss)", idle)

    def _digest(self, event_id, text, source, captured, contact, images, known=None) -> digest.Digest:
        row = self.conn.execute("SELECT body FROM digests WHERE event_id = ?", (event_id,)).fetchone()
        if row:
            return digest.from_json(row["body"])
        kind = {"call": "통화 녹취(STT)", "voice": "음성 메모(STT)", "share": "공유 자료"}[source]
        raw, used = self.router.chat_json("summarize", digest.build_messages(text, kind, captured, contact, known),
                                          digest.SCHEMA, images=images or None)
        d = digest.parse(raw, used)
        self.conn.execute("INSERT INTO digests VALUES (?, ?, ?, ?)", (event_id, digest.to_json(d), used, db.now()))
        return d

    def _register(self, cid: str, event_id: str, c: Candidate, model: str, source: str, note: str) -> notify.Line:
        fields = {"title": c.title, "demoted": c.demoted,
                  "start": c.when.start.isoformat() if c.when else None,
                  "end": c.when.end.isoformat() if c.when else None,
                  "all_day": c.when.all_day if c.when else None,
                  "expr": {"date": c.extraction.date_expr, "time": c.extraction.time_expr,
                           "duration": c.extraction.duration_expr},
                  "people": c.extraction.people, "project": c.extraction.project}
        assumed = sorted(c.when.assumed) if c.when else []
        self.conn.execute(
            """INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
               ON CONFLICT(cand_id) DO UPDATE SET fields=excluded.fields, assumed=excluded.assumed,
               confidence=excluded.confidence, model=excluded.model""",
            (cid, event_id, c.kind, json.dumps(fields, ensure_ascii=False), json.dumps(assumed),
             c.confidence, model, self.s.calendar_id))
        when = _when_text(c, self.tz)
        base = dict(cand_id=cid, kind=c.kind, title=c.title, when=when, assumed=bool(assumed))

        calendarable = c.kind in ("schedule", "deadline") and c.when is not None
        if source == "share" and not self.s.calendar_from_share:
            calendarable = False  # 자료 속 날짜는 통화보다 오탐이 많아 기본은 노트에만 적는다
        if not calendarable:
            return notify.Line(**base, state="not_calendar")
        if c.confidence < self.s.min_confidence:
            return notify.Line(**base, state="low_confidence")
        if self._undone(cid):
            # 사용자가 되돌린 후보는 재처리해도 다시 넣지 않는다
            return notify.Line(**base, state="skipped_deleted")
        w = self.calendar.upsert(gcal.event_id(cid), gcal.build_body(c, cid, self.s.timezone, note))
        action = "dry_run" if w.status == "dry_run" else "auto_register"
        if w.status != "skipped_deleted":
            self.conn.execute("INSERT INTO decisions VALUES (?, ?, NULL, ?, ?)",
                              (cid, action, db.now(), None if w.status == "dry_run" else w.event_id))
        state = {"dry_run": "dry_run", "skipped_deleted": "skipped_deleted"}.get(w.status, "registered")
        return notify.Line(**base, state=state, link=w.html_link)

    def _undone(self, cid: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM decisions WHERE cand_id = ? AND action = 'undo' LIMIT 1", (cid,)).fetchone() is not None

    # ── 되돌리기 (spec 10.3) ────────────────────────────────────────────

    def undo(self, ident: str) -> list[str]:
        """후보 id 앞자리 또는 이벤트 id. 이벤트 id면 그 이벤트에서 등록한 일정 전부를 지운다."""
        if len(ident) < 8:
            raise ValueError("id는 8자 이상")
        rows = self.conn.execute(
            """SELECT c.cand_id, c.fields FROM candidates c
               WHERE c.cand_id LIKE ? OR c.event_id = ?""", (ident + "%", ident)).fetchall()
        done = []
        for r in rows:
            cid = r["cand_id"]
            last = self.conn.execute(
                "SELECT action FROM decisions WHERE cand_id = ? ORDER BY decided_at DESC, rowid DESC LIMIT 1",
                (cid,)).fetchone()
            if last is None or last["action"] not in ("auto_register", "dry_run"):
                continue
            if last["action"] == "auto_register":
                self.calendar.delete(gcal.event_id(cid))
            self.conn.execute("INSERT INTO decisions VALUES (?, 'undo', NULL, ?, ?)",
                              (cid, db.now(), gcal.event_id(cid)))
            done.append(f"{cid[:12]} {json.loads(r['fields'])['title']}")
        return done


def _card_md(card: dict) -> list[str]:
    """명함 노트의 핵심 줄. key_points 자리에 넣는다."""
    rows = [("소속", card["org"]), ("직함", card["title"]), ("전화", ", ".join(card["phones"])),
            ("이메일", ", ".join(card["emails"])), ("주소", card["address"])]
    return [f"{k}: {v}" for k, v in rows if v]
