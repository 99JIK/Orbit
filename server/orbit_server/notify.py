"""검토 알림. 일정은 바로 등록하고, 사용자는 메시지 한 번으로 확인하고 고치거나 되돌린다.

디스코드 봇(spec 6장)이 생기기 전까지는 웹훅으로 `#approve` 채널에 보낸다. 웹훅은 단방향이라 버튼이 없다.
되돌리기는 Calendar 링크(폰에서 바로 열림)나 `orbit-server undo <id>`로 한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

LIMIT = 1900  # 디스코드 content 2000자 제한에 여유를 둔다

KIND_LABEL = {"schedule": "일정", "deadline": "마감", "task": "할 일", "note": "메모"}


@dataclass(frozen=True)
class Line:
    cand_id: str
    kind: str
    title: str
    when: str  # 사람이 읽는 일시. 없으면 ""
    assumed: bool
    state: str  # registered | dry_run | low_confidence | not_calendar | skipped_deleted
    link: str | None = None


def format_review(source: str, title: str, note: str, lines: list[Line], dry_reason: str = "") -> str:
    head = {"call": "통화 정리", "voice": "음성 메모 정리", "share": "공유 자료 정리"}.get(source, "정리")
    out = [f"**[Orbit] {head}: {title}**", f"노트: `{note}`"]
    reg = [x for x in lines if x.state in ("registered", "dry_run", "skipped_deleted")]
    check = [x for x in lines if x.state == "low_confidence"]
    todos = [x for x in lines if x.state == "not_calendar"]
    if reg:
        tag = f" (dry-run: {dry_reason})" if any(x.state == "dry_run" for x in reg) else ""
        out.append(f"\n캘린더 등록 {len(reg)}건{tag}")
        for x in reg:
            extra = " (추정 포함)" if x.assumed else ""
            if x.state == "skipped_deleted":
                extra += " (이전에 캘린더에서 지워 건너뜀)"
            out.append(f"- {x.when} {KIND_LABEL.get(x.kind, x.kind)} **{x.title}**{extra}")
            if x.link:
                out.append(f"  수정·삭제: <{x.link}>")
            out.append(f"  되돌리기: `orbit-server undo {x.cand_id[:12]}`")
    if check:
        out.append("\n확인 필요 (확신도 낮아 등록 안 함)")
        out += [f"- {x.when} {x.title}".replace("-  ", "- ") for x in check]
    if todos:
        out.append("\n노트에만 적음")
        out += [f"- {KIND_LABEL.get(x.kind, x.kind)} {x.title}" + (f" ({x.when})" if x.when else "") for x in todos]
    msg = "\n".join(out)
    return msg if len(msg) <= LIMIT else msg[: LIMIT - 20] + "\n...(생략)"


class StdoutNotifier:
    def send(self, text: str) -> None:
        print(text, flush=True)


class DiscordWebhook:
    def __init__(self, url: str):
        if not url:
            raise ValueError("디스코드 웹훅 URL이 비어 있음")
        self.url = url

    def send(self, text: str) -> None:
        r = httpx.post(self.url, json={"content": text, "allowed_mentions": {"parse": []}}, timeout=20)
        r.raise_for_status()
