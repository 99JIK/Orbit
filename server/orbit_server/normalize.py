"""날짜 정규화 (spec 5장 4단계).

SLM이 추출한 원문 표현(`date_expr`, `time_expr`, `duration_expr`)을 캡처 시각 기준의 절대 시각으로 바꾼다.
SLM은 날짜를 계산하지 않는다. 계산은 전부 이 모듈과 로케일 규칙이 맡는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .locales import ko
from .locales.ko import Defaults


@dataclass(frozen=True)
class Resolved:
    start: datetime
    end: datetime
    all_day: bool
    #: 기본값이나 추정으로 채운 필드: date, time, meridiem, duration. 후보 카드에 "추정"으로 표시한다.
    assumed: frozenset[str]


def resolve(
    date_expr: str | None,
    time_expr: str | None,
    duration_expr: str | None,
    captured_at: datetime,
    defaults: Defaults = Defaults(),
) -> Resolved | None:
    """해석할 수 없으면 None을 돌려준다. 호출자는 일정 후보를 할 일로 강등한다 (spec 5.1)."""
    if captured_at.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware")
    if not date_expr and not time_expr:
        return None

    for expr in (time_expr, date_expr):
        if expr and (offset := ko.parse_relative_time(expr)):
            start = captured_at + offset
            return Resolved(start, start + defaults.duration, all_day=False, assumed=frozenset({"duration"}))

    today = captured_at.date()
    assumed: set[str] = set()

    day = None
    if date_expr:
        parsed_date = ko.parse_date(date_expr, today)
        if parsed_date is None:
            return None
        day = parsed_date.value
        if parsed_date.assumed:
            assumed.add("date")

    # SLM이 시각을 date_expr에 함께 넣는 경우("내일 오후 3시")를 흡수한다.
    parsed_time = ko.parse_time(time_expr or date_expr or "", defaults)
    if parsed_time is None and time_expr and day is None:
        return None

    if parsed_time is None:
        start = datetime.combine(day, datetime.min.time(), captured_at.tzinfo)
        return Resolved(start, start + timedelta(days=1), all_day=True, assumed=frozenset(assumed))

    assumed |= parsed_time.assumed
    if day is None:
        # 시각만 있으면 오늘, 이미 지난 시각이면 내일로 본다.
        day = today
        if _at(day, parsed_time.start, captured_at) <= captured_at:
            day = today + timedelta(days=1)
        assumed.add("date")

    start = _at(day, parsed_time.start, captured_at)
    if parsed_time.end is not None:
        end = _at(day, parsed_time.end, captured_at)
    elif duration_expr and (duration := ko.parse_duration(duration_expr)):
        end = start + duration
    else:
        end = start + defaults.duration
        assumed.add("duration")
    return Resolved(start, end, all_day=False, assumed=frozenset(assumed))


def _at(day, clock: ko.Clock, captured_at: datetime) -> datetime:
    return datetime.combine(day + timedelta(days=clock.day_offset), clock.value, captured_at.tzinfo)
