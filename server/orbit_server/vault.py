"""지식 볼트 쓰기 (spec 18.1).

Orbit은 `00 Inbox/` 아래에 새 파일을 만들기만 한다. 기존 노트 수정·이동은 승인 흐름을 거쳐야 하므로 여기서 하지 않는다.
같은 이벤트를 다시 처리하면 frontmatter의 `event_id`로 알아보고 같은 파일을 덮어쓴다.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .people import Person

INBOX = "00 Inbox"
ORIGINAL_TAG = "(원본)"
EMBEDDABLE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"}

_BAD = re.compile(r'[\\/:*?"<>|#^\[\]\n\r\t]')


def safe_name(s: str, limit_bytes: int = 180) -> str:
    """파일명. Obsidian 링크를 깨는 #^[]|도 뺀다. 한글은 3바이트라 글자 수가 아니라 바이트로 자른다."""
    s = re.sub(r"\s+", " ", _BAD.sub(" ", s)).strip(" .")
    b = s.encode()
    if len(b) > limit_bytes:
        s = b[:limit_bytes].decode(errors="ignore")
    return s.rstrip(" .") or "untitled"


def frontmatter(meta: dict) -> str:
    out = ["---"]
    for k, v in meta.items():
        if v is None or v == "":
            continue
        if isinstance(v, list):
            out.append(f"{k}: []" if not v else f"{k}:")
            out += [f"  - {json.dumps(x, ensure_ascii=False)}" for x in v]
        elif isinstance(v, (bool, int, float)):
            out.append(f"{k}: {json.dumps(v)}")
        else:
            # JSON 문자열은 그대로 유효한 YAML 스칼라라서 이스케이프를 json에 맡긴다
            out.append(f"{k}: {json.dumps(str(v), ensure_ascii=False)}")
    out.append("---")
    return "\n".join(out)


class Vault:
    def __init__(self, root: Path):
        self.root = root
        self.inbox = root / INBOX

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    def _path_for(self, stem: str, event_id: str) -> Path:
        self.inbox.mkdir(parents=True, exist_ok=True)
        for existing in self.inbox.glob("*.md"):
            # 재처리: 제목이 바뀌어도 같은 이벤트의 노트를 찾아 덮어쓴다
            head = existing.read_text(encoding="utf-8", errors="replace")[:2000]
            if f'event_id: "{event_id}"' in head:
                if existing.stem != stem:
                    target = self._free(stem)
                    existing.rename(target)
                    return target
                return existing
        return self._free(stem)

    def _free(self, stem: str) -> Path:
        p = self.inbox / f"{stem}.md"
        n = 2
        while p.exists():
            p = self.inbox / f"{stem} {n}.md"
            n += 1
        return p

    def write(self, *, event_id: str, source: str, captured_at: datetime, title: str, summary: str,
              key_points: list[str], people: list[dict], tags: list[str], items: list[str],
              transcript: str | None = None, extracted: str | None = None, original: Path | None = None,
              source_url: str = "", contact: str = "") -> Path:
        who = f"{contact} " if contact and not title.startswith(contact) else ""
        stem = safe_name(f"{captured_at:%Y-%m-%d %H%M} {who}{title}")
        path = self._path_for(stem, event_id)
        meta = {
            "type": source,
            "created": captured_at.isoformat(timespec="minutes"),
            "contact": contact,
            "people": [f"[[{safe_name(p['name'])}]]" for p in people],
            "tags": [source, *tags],
            "source": source_url,
            "event_id": event_id,
        }
        body = [frontmatter(meta), f"\n# {title}\n", "## 요약", summary or "(없음)"]
        if key_points:
            body += ["\n## 핵심", *[f"- {k}" for k in key_points]]
        if people:
            body += ["\n## 사람", *[f"- [[{safe_name(p['name'])}]]" + (f": {p['note']}" if p["note"] else "")
                                   for p in people]]
        if items:
            body += ["\n## 일정·할 일", *items]
        if original is not None:
            orig = path.with_name(f"{path.stem} {ORIGINAL_TAG}{original.suffix.lower()}")
            shutil.copy2(original, orig)
            embed = "!" if original.suffix.lower() in EMBEDDABLE else ""
            body += ["\n## 원본", f"{embed}[[{orig.name}]]"]
        if source_url:
            body.append(f"- 링크: <{source_url}>")
        if transcript:
            # 본인이 참여한 통화·본인 음성 메모의 STT는 볼트에 전문을 둘 수 있다 (spec 9.4)
            body += ["\n## 전사", "> [!note]- 전사문 (로컬 STT)", *[f"> {ln}" for ln in transcript.splitlines()]]
        if extracted:
            body += ["\n> [!quote]- 추출 텍스트", *[f"> {ln}" for ln in extracted[:20000].splitlines()]]
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        return path

    def write_person(self, p: Person) -> Path:
        """사람 노트. 볼트 어디에든 같은 이름 노트가 있으면 건드리지 않는다(사용자가 옮기거나 고쳤을 수 있다).

        없을 때만 `00 Inbox/<이름>.md`를 새로 만든다. 이후 관계·메모가 바뀌어도 노트는 고치지 않고 운영 DB만 바뀐다.
        통화 노트의 `[[이름]]` 링크가 이 노트로 이어진다.
        """
        stem = safe_name(p.name)
        for existing in self.root.rglob(f"{stem}.md"):
            if ".obsidian" not in existing.parts:
                return existing
        self.inbox.mkdir(parents=True, exist_ok=True)
        path = self.inbox / f"{stem}.md"
        meta = {
            "type": "person",
            "aliases": p.aliases,
            "relation": p.relation,
            "org": p.org,
            "title": p.title,
            "phones": p.phones,
            "emails": p.emails,
            "tags": ["person"],
        }
        body = [frontmatter(meta), f"\n# {p.name}\n"]
        info = [x for x in (p.relation, " ".join(y for y in (p.org, p.title) if y)) if x]
        if info:
            body.append(" · ".join(info))
        if p.note:
            body += ["\n## 메모", p.note]
        body += ["\n## 기록", "이 사람이 나온 통화·자료 노트는 Obsidian 백링크로 모인다."]
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        return path
