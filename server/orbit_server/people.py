"""사람 사전 (spec 8.3 맥락 사전의 `people`).

통화 상대 번호로 사람을 찾아 STT 힌트와 요약 맥락에 이름·관계를 넣고, 노트의 사람 링크를 정식 이름으로 맞춘다.

원천은 두 곳이다.
- 운영 DB `people` 테이블: 폰 앱에서 번호에 붙인 관계·메모(`contact` 이벤트), 명함에서 뽑은 연락처
- `config/context.yaml`의 `people`: 사용자가 직접 적는 항목. 읽기만 하고, 같은 사람이면 DB 값보다 우선한다
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    person_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    aliases     TEXT NOT NULL DEFAULT '[]',
    phones      TEXT NOT NULL DEFAULT '[]',
    emails      TEXT NOT NULL DEFAULT '[]',
    org         TEXT,
    title       TEXT,
    relation    TEXT,
    note        TEXT,
    source      TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def normalize_phone(number: str | None) -> str:
    """비교용 번호. 숫자만 남기고 +82는 국내 0으로 바꾼다. 예: +82 10-1234-5678 -> 01012345678"""
    digits = re.sub(r"\D", "", number or "")
    if digits.startswith("82") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits


@dataclass
class Person:
    name: str
    aliases: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    org: str = ""
    title: str = ""
    relation: str = ""
    note: str = ""
    person_id: int | None = None

    def hint_names(self) -> list[str]:
        """STT initial prompt에 넣을 고유명사. 소속도 통화에서 자주 나온다."""
        return [self.name, *self.aliases, *([self.org] if self.org else [])]

    def describe(self) -> str:
        """요약 프롬프트용 한 줄. 예: 김민수 (지도교수, OO대학교 교수) 메모: ..."""
        tags = [x for x in (self.relation, " ".join(y for y in (self.org, self.title) if y)) if x]
        s = self.name + (f" ({', '.join(tags)})" if tags else "")
        if self.aliases:
            s += f" 별칭: {', '.join(self.aliases)}"
        if self.note:
            s += f" 메모: {self.note}"
        return s


def _row(r: sqlite3.Row) -> Person:
    return Person(name=r["name"], aliases=json.loads(r["aliases"]), phones=json.loads(r["phones"]),
                  emails=json.loads(r["emails"]), org=r["org"] or "", title=r["title"] or "",
                  relation=r["relation"] or "", note=r["note"] or "", person_id=r["person_id"])


class People:
    def __init__(self, conn: sqlite3.Connection, context_file: Path | None = None):
        self.conn = conn
        self.conn.executescript(SCHEMA)
        self.context = _load_context(context_file) if context_file else []

    def all(self) -> list[Person]:
        rows = [_row(r) for r in self.conn.execute("SELECT * FROM people ORDER BY name")]
        return _merge(self.context, rows)

    def by_phone(self, number: str | None) -> Person | None:
        key = normalize_phone(number)
        if not key:
            return None
        for p in self.all():
            if key in {normalize_phone(x) for x in p.phones}:
                return p
        return None

    def by_name(self, name: str) -> Person | None:
        n = name.strip()
        for p in self.all():
            if n == p.name or n in p.aliases:
                return p
        return None

    def canonical(self, name: str) -> str:
        p = self.by_name(name)
        return p.name if p else name

    def upsert(self, *, name: str, source: str, phones: list[str] | None = None, emails: list[str] | None = None,
               org: str = "", title: str = "", relation: str | None = None, note: str | None = None,
               aliases: list[str] | None = None) -> tuple[Person, bool]:
        """번호가 같으면 같은 사람, 번호가 없으면 이름이 같으면 같은 사람으로 본다. (사람, 새로 만들었는지)

        빈 값은 기존 값을 지우지 않는다. 관계·메모는 None이 아니면 덮어쓴다(앱에서 지운 경우 빈 문자열).
        """
        phones = [p for p in (phones or []) if normalize_phone(p)]
        existing = None
        for ph in phones:
            existing = self._db_by_phone(ph)
            if existing:
                break
        if existing is None and name:
            r = self.conn.execute("SELECT * FROM people WHERE name = ? LIMIT 1", (name,)).fetchone()
            existing = _row(r) if r else None
        if existing is None:
            cur = self.conn.execute(
                "INSERT INTO people (name, aliases, phones, emails, org, title, relation, note, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, json.dumps(aliases or [], ensure_ascii=False), json.dumps(_dedupe_phones(phones)),
                 json.dumps(emails or []), org or None, title or None, relation or None, note or None, source,
                 db.now()))
            return self._get(cur.lastrowid), True
        p = existing
        merged_phones = _dedupe_phones([*p.phones, *phones])
        merged_emails = list(dict.fromkeys([*p.emails, *(emails or [])]))
        merged_aliases = list(dict.fromkeys([*p.aliases, *(aliases or [])]))
        # 이름이 바뀌면(앱에서 다른 이름으로 저장) 옛 이름은 별칭으로 남겨 예전 노트 링크와 이어지게 한다
        if name and name != p.name and p.name not in merged_aliases:
            merged_aliases.append(p.name)
        self.conn.execute(
            "UPDATE people SET name=?, aliases=?, phones=?, emails=?, org=?, title=?, relation=?, note=?, "
            "updated_at=? WHERE person_id=?",
            (name or p.name, json.dumps([a for a in merged_aliases if a != (name or p.name)], ensure_ascii=False),
             json.dumps(merged_phones), json.dumps(merged_emails), org or p.org or None, title or p.title or None,
             p.relation if relation is None else (relation or None), p.note if note is None else (note or None),
             db.now(), p.person_id))
        return self._get(p.person_id), False

    def _db_by_phone(self, number: str) -> Person | None:
        key = normalize_phone(number)
        for r in self.conn.execute("SELECT * FROM people"):
            p = _row(r)
            if key in {normalize_phone(x) for x in p.phones}:
                return p
        return None

    def _get(self, person_id: int) -> Person:
        return _row(self.conn.execute("SELECT * FROM people WHERE person_id = ?", (person_id,)).fetchone())


def _dedupe_phones(phones: list[str]) -> list[str]:
    seen, out = set(), []
    for p in phones:
        k = normalize_phone(p)
        if k and k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _load_context(path: Path) -> list[Person]:
    if not path.exists():
        return []
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for p in data.get("people") or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        out.append(Person(
            name=str(p["name"]), aliases=[str(a) for a in p.get("aliases") or []],
            phones=[str(x) for x in p.get("phones") or []], emails=[str(x) for x in p.get("emails") or []],
            org=str(p.get("org") or ""), title=str(p.get("title") or ""),
            relation=str(p.get("role") or p.get("relation") or ""), note=str(p.get("note") or ""),
        ))
    return out


def _merge(context: list[Person], rows: list[Person]) -> list[Person]:
    """같은 사람(번호나 이름이 겹침)이면 context.yaml 값을 앞세우고 빈 칸만 DB 값으로 채운다."""
    out = []
    used = set()
    for c in context:
        keys = {normalize_phone(x) for x in c.phones} - {""}
        names = {c.name, *c.aliases}
        for i, r in enumerate(rows):
            if i in used:
                continue
            if keys & {normalize_phone(x) for x in r.phones} or r.name in names:
                used.add(i)
                c = Person(
                    name=c.name, aliases=list(dict.fromkeys([*c.aliases, *r.aliases])),
                    phones=_dedupe_phones([*c.phones, *r.phones]), emails=list(dict.fromkeys([*c.emails, *r.emails])),
                    org=c.org or r.org, title=c.title or r.title, relation=c.relation or r.relation,
                    note=c.note or r.note, person_id=r.person_id,
                )
        out.append(c)
    out += [r for i, r in enumerate(rows) if i not in used]
    return out
