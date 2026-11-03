"""후보 생성: 추출 결과에 날짜 정규화를 적용한다 (spec 5장 3~4단계, 5.1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .extract import ChatModel, Extraction, extract
from .locales.ko import Defaults
from .normalize import Resolved, resolve


@dataclass(frozen=True)
class Candidate:
    kind: str
    title: str
    confidence: float
    when: Resolved | None
    #: 일정·마감이었으나 날짜를 해석하지 못해 할 일로 낮춘 경우 (spec 5.1)
    demoted: bool
    extraction: Extraction


def build_candidate(
    text: str,
    captured_at: datetime,
    model: ChatModel,
    defaults: Defaults = Defaults(),
) -> Candidate:
    return candidate_from(extract(text, model), captured_at, defaults)


def candidate_from(ex: Extraction, captured_at: datetime, defaults: Defaults = Defaults()) -> Candidate:
    when = None
    if ex.kind != "ignore":
        when = resolve(ex.date_expr, ex.time_expr, ex.duration_expr, captured_at, defaults)

    kind, demoted = ex.kind, False
    if kind in ("schedule", "deadline") and when is None:
        kind, demoted = "task", True
    return Candidate(kind=kind, title=ex.title, confidence=ex.confidence, when=when, demoted=demoted, extraction=ex)
