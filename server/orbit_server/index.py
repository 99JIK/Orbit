"""볼트 검색 인덱스 (spec 8.2 `data/index.db`, 18.4).

파생물이다. 지우면 다음 검색 때 볼트에서 다시 만든다. 검색할 때마다 파일 수정 시각을 보고 바뀐 노트만 다시 색인한다.

한국어는 SQLite FTS5 trigram 토크나이저로 색인한다. trigram은 3글자 미만 검색어(회의, 마감 같은 2음절 단어)를
MATCH로 못 찾으므로 그런 검색어는 LIKE로 거른다. 개인 볼트 규모에서는 LIKE 전체 훑기도 충분히 빠르다.
벡터 검색(spec 18.4 3단계)은 아직 없다.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    path        TEXT PRIMARY KEY,
    mtime       REAL NOT NULL,
    content_hash TEXT NOT NULL,
    type        TEXT,
    title       TEXT,
    created     TEXT,
    event_id    TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    path UNINDEXED, ordinal UNINDEXED, section UNINDEXED, text, tokenize = 'trigram'
);
"""

#: 원 통화 녹취가 들어 있는 절. 요약만 허용된 클라이언트에는 이 절을 넘기지 않는다
TRANSCRIPT_SECTION = "전사"
CHUNK_CHARS = 800


@dataclass(frozen=True)
class Hit:
    path: str
    title: str
    type: str
    created: str
    section: str
    snippet: str


def _frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
        if m and m.group(2):
            meta[m.group(1)] = m.group(2).strip().strip('"')
    return meta, text[end + 4:].lstrip("\n")


def split_sections(body: str) -> list[tuple[str, str]]:
    """(절 제목, 내용). `## 제목` 기준. 첫 절은 제목 없음."""
    out, cur, buf = [], "", []
    for line in body.splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            if buf:
                out.append((cur, "\n".join(buf).strip()))
            cur, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    if buf:
        out.append((cur, "\n".join(buf).strip()))
    return [(s, t) for s, t in out if t]


def strip_sections(text: str, section: str) -> str:
    """노트에서 한 절(다음 `## `까지)을 뺀다. 요약만 허용된 클라이언트용."""
    out, skip = [], False
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            skip = m.group(1).strip() == section
            if skip:
                out.append(f"## {section}\n(이 클라이언트에는 원 녹취를 보여 주지 않습니다)")
                continue
        if not skip:
            out.append(line)
    return "\n".join(out)


def _chunks(body: str) -> list[tuple[str, str]]:
    out = []
    for section, text in split_sections(body):
        # 전사 callout의 "> " 접두는 검색에 방해만 된다
        text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
        while text:
            piece, text = text[:CHUNK_CHARS], text[CHUNK_CHARS:]
            out.append((section, piece))
    return out


class Index:
    def __init__(self, db_path: Path, vault: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.vault = vault

    def refresh(self) -> int:
        """바뀐 노트만 다시 색인. 지워진 노트는 색인에서 뺀다. 다시 색인한 노트 수."""
        seen, changed = set(), 0
        if self.vault.is_dir():
            for p in self.vault.rglob("*.md"):
                if ".obsidian" in p.parts or ".trash" in p.parts:
                    continue
                rel = p.relative_to(self.vault).as_posix()
                seen.add(rel)
                mtime = p.stat().st_mtime
                row = self.conn.execute("SELECT mtime FROM notes WHERE path = ?", (rel,)).fetchone()
                if row and row["mtime"] == mtime:
                    continue
                self._index(rel, p.read_text(encoding="utf-8", errors="replace"), mtime)
                changed += 1
        for (rel,) in self.conn.execute("SELECT path FROM notes").fetchall():
            if rel not in seen:
                self.conn.execute("DELETE FROM notes WHERE path = ?", (rel,))
                self.conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))
        return changed

    def _index(self, rel: str, text: str, mtime: float) -> None:
        meta, body = _frontmatter(text)
        title = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), Path(rel).stem)
        h = hashlib.sha256(text.encode()).hexdigest()
        self.conn.execute("BEGIN")
        self.conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))
        self.conn.execute(
            "INSERT OR REPLACE INTO notes VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rel, mtime, h, meta.get("type", "note"), title, meta.get("created", ""), meta.get("event_id")))
        # 제목도 검색되게 첫 조각으로 넣는다
        rows = [("", title), *_chunks(body)]
        self.conn.executemany("INSERT INTO chunks (path, ordinal, section, text) VALUES (?, ?, ?, ?)",
                              [(rel, i, s, t) for i, (s, t) in enumerate(rows)])
        self.conn.execute("COMMIT")

    def search(self, query: str, *, limit: int = 8, types: list[str] | None = None, since: str = "",
               until: str = "", include_transcripts: bool = False) -> list[Hit]:
        """검색어를 공백으로 나눠 모두 들어 있는 조각을 찾는다. 노트당 가장 맞는 조각 하나."""
        self.refresh()
        terms = [t for t in query.split() if t]
        if not terms:
            return []
        long_terms = [t for t in terms if len(t) >= 3]
        where, args = [], []
        if long_terms:
            where.append("chunks MATCH ?")
            args.append(" AND ".join('"' + t.replace('"', '""') + '"' for t in long_terms))
        for t in terms:
            if len(t) < 3:
                where.append("chunks.text LIKE ?")
                args.append(f"%{t}%")
        if not include_transcripts:
            where.append("chunks.section != ?")
            args.append(TRANSCRIPT_SECTION)
        if types:
            where.append(f"notes.type IN ({','.join('?' for _ in types)})")
            args += types
        if since:
            where.append("notes.created >= ?")
            args.append(since)
        if until:
            where.append("notes.created < ?")
            args.append(until)
        order = "bm25(chunks)" if long_terms else "notes.created DESC"
        rows = self.conn.execute(
            f"""SELECT chunks.path, chunks.section, chunks.text, notes.title, notes.type, notes.created
                FROM chunks JOIN notes ON notes.path = chunks.path
                WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?""",
            (*args, limit * 5)).fetchall()
        hits, used = [], set()
        for r in rows:
            if r["path"] in used:
                continue
            used.add(r["path"])
            hits.append(Hit(r["path"], r["title"], r["type"] or "", r["created"] or "", r["section"],
                            _snippet(r["text"], terms)))
            if len(hits) >= limit:
                break
        return hits

    def recent(self, *, limit: int = 20, types: list[str] | None = None, since: str = "") -> list[sqlite3.Row]:
        self.refresh()
        where, args = ["1=1"], []
        if types:
            where.append(f"type IN ({','.join('?' for _ in types)})")
            args += types
        if since:
            where.append("created >= ?")
            args.append(since)
        return self.conn.execute(
            f"SELECT path, title, type, created FROM notes WHERE {' AND '.join(where)} "
            "ORDER BY created DESC, mtime DESC LIMIT ?", (*args, limit)).fetchall()

    def mentioning(self, name: str, limit: int = 10) -> list[sqlite3.Row]:
        """`[[이름]]` 링크가 있는 노트. 사람 노트의 백링크."""
        self.refresh()
        return self.conn.execute(
            """SELECT DISTINCT notes.path, notes.title, notes.type, notes.created FROM chunks
               JOIN notes ON notes.path = chunks.path WHERE chunks.text LIKE ? AND notes.type != 'person'
               ORDER BY notes.created DESC LIMIT ?""", (f"%[[{name}]]%", limit)).fetchall()


def _snippet(text: str, terms: list[str], width: int = 240) -> str:
    low = text.lower()
    pos = min((low.find(t.lower()) for t in terms if low.find(t.lower()) >= 0), default=0)
    start = max(0, pos - width // 3)
    s = text[start:start + width].replace("\n", " ").strip()
    return ("…" if start else "") + s + ("…" if start + width < len(text) else "")
