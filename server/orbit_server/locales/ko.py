"""한국어 날짜·시간 표현 해석 (docs/normalize-ko.md).

SLM이 추출한 원문 표현을 받아 날짜·시각·기간으로 바꾼다. 계산 기준은 캡처 날짜이며,
해석 규칙이 기본값이나 추정에 기댄 경우 `assumed`로 표시한다.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta

# ── 숫자 ────────────────────────────────────────────────────────────────

_NATIVE = {
    "열두": 12, "열한": 11, "열": 10,
    "한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "석": 3, "네": 4, "넷": 4, "넉": 4,
    "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9,
}
_SINO_DIGITS = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_DAY_COUNTS = {
    "하루": 1, "이틀": 2, "사흘": 3, "나흘": 4, "닷새": 5, "엿새": 6,
    "이레": 7, "여드레": 8, "아흐레": 9, "열흘": 10, "보름": 15,
}

# 정규식 교대 순서는 긴 것부터: "열두"가 "두"보다 먼저 맞아야 한다.
NATIVE = "|".join(sorted(_NATIVE, key=len, reverse=True))
NUM = rf"(?:\d{{1,2}}|{NATIVE})"
SINO = r"[일이삼사오육칠팔구십]+"


def to_int(token: str) -> int:
    """아라비아 숫자, 고유어 수(한·두·열두), 한자어 수(삼십·십오)를 정수로."""
    if token.isdigit():
        return int(token)
    if token in _NATIVE:
        return _NATIVE[token]
    return _sino(token)


def _sino(token: str) -> int:
    if "십" not in token:
        return _SINO_DIGITS[token]
    tens, _, ones = token.partition("십")
    return (_SINO_DIGITS[tens] if tens else 1) * 10 + (_SINO_DIGITS[ones] if ones else 0)


def compact(expr: str) -> str:
    """공백을 없애고 해석에 영향이 없는 조사를 뗀다. STT·SLM 출력의 띄어쓰기 편차를 흡수한다."""
    s = re.sub(r"\s+", "", expr)
    return re.sub(r"(?:에서|에|쯤|경|정도|중|까지)$", "", s)


# ── 날짜 ────────────────────────────────────────────────────────────────

_WEEKDAYS = "월화수목금토일"
_WEEK_OFFSETS = {
    "지지난": -2, "지난": -1, "저번": -1,
    "이번": 0, "금": 0,
    "다음": 1, "담": 1, "차": 1, "내": 1,
    "다다음": 2,
}
_WEEK_PREFIX = "|".join(sorted(_WEEK_OFFSETS, key=len, reverse=True))
_MONTH_OFFSETS = {"지난": -1, "저번": -1, "이번": 0, "이": 0, "다음": 1, "담": 1, "다다음": 2}
_MONTH_PREFIX = "|".join(sorted(_MONTH_OFFSETS, key=len, reverse=True))
_DAY_WORDS = {
    "그저께": -2, "그제": -2, "어제": -1, "오늘": 0,
    "내일모레": 2, "내일": 1, "모레": 2, "글피": 3,
}


@dataclass(frozen=True)
class DateResult:
    value: date
    assumed: bool = False


def parse_date(expr: str, today: date) -> DateResult | None:
    s = compact(expr)
    for pattern, handler in _DATE_RULES:
        if m := re.search(pattern, s):
            return handler(m, today)
    return None


def _absolute(m: re.Match, today: date) -> DateResult:
    return DateResult(date(int(m["y"]), int(m["m"]), int(m["d"])))


def _month_day(m: re.Match, today: date) -> DateResult:
    month, day = int(m["m"]), int(m["d"])
    candidate = date(today.year, month, day)
    # 일주일 넘게 지난 날짜는 내년으로 본다. 그보다 가까운 과거는 그대로 두고 검증 단계가 거른다.
    if candidate < today - timedelta(days=7):
        candidate = date(today.year + 1, month, day)
    return DateResult(candidate)


def _relative_month(m: re.Match, today: date) -> DateResult:
    year, month = _add_months(today.year, today.month, _MONTH_OFFSETS[m["p"]] if m["p"] else 0)
    last = calendar.monthrange(year, month)[1]
    part = m["part"]
    if part.endswith("일"):
        return DateResult(date(year, month, min(int(part[:-1]), last)))
    day = {"초": 1, "중순": 15, "말": last}[part]
    return DateResult(date(year, month, day), assumed=True)


def _after_days(m: re.Match, today: date) -> DateResult:
    n = _DAY_COUNTS[m["w"]] if m["w"] else to_int(m["n"])
    return DateResult(today + timedelta(days=n))


def _after_weeks(m: re.Match, today: date) -> DateResult:
    n = to_int(m["n"]) if m["n"] else 1
    return DateResult(today + timedelta(weeks=n))


def _after_months(m: re.Match, today: date) -> DateResult:
    year, month = _add_months(today.year, today.month, to_int(m["n"]))
    return DateResult(date(year, month, min(today.day, calendar.monthrange(year, month)[1])))


def _day_word(m: re.Match, today: date) -> DateResult:
    return DateResult(today + timedelta(days=_DAY_WORDS[m[0]]))


def _month_part(m: re.Match, today: date) -> DateResult:
    month = int(m["m"])
    year = today.year + 1 if month < today.month else today.year
    last = calendar.monthrange(year, month)[1]
    return DateResult(date(year, month, {"초": 1, "중순": 15, "말": last}[m["part"]]), assumed=True)


def _monday(today: date) -> date:
    """주는 월요일에 시작한다."""
    return today - timedelta(days=today.weekday())


def _week_only(m: re.Match, today: date) -> DateResult:
    """요일 없는 "다음 주"는 그 주 금요일(업무 마지막 날)로 추정한다."""
    return DateResult(_monday(today) + timedelta(weeks=_WEEK_OFFSETS[m["p"]], days=4), assumed=True)


def _weekend(m: re.Match, today: date) -> DateResult:
    """주말은 토요일로 추정한다. "다음 주말"은 "다음 주"의 "주"와 "주말"의 "주"가 겹친 표현이다."""
    prefix = m["p"]
    result = _monday(today) + timedelta(weeks=_WEEK_OFFSETS[prefix] if prefix else 0, days=5)
    if not prefix and result < today:
        result += timedelta(weeks=1)
    return DateResult(result, assumed=True)


def _week_day(m: re.Match, today: date) -> DateResult:
    prefix = m["p"]
    weekday = _WEEKDAYS.index(m["t"])
    if prefix:
        return DateResult(_monday(today) + timedelta(weeks=_WEEK_OFFSETS[prefix], days=weekday))
    # 요일만 말하면 오늘 이후 가장 가까운 그 요일. 오늘과 같은 요일이면 다음 주다 ("오늘"이라고 했을 것이므로).
    ahead = (weekday - today.weekday()) % 7 or 7
    return DateResult(today + timedelta(days=ahead))


def _bare_day(m: re.Match, today: date) -> DateResult:
    day = int(m["d"])
    year, month = today.year, today.month
    if day < today.day:
        year, month = _add_months(year, month, 1)
    return DateResult(date(year, month, min(day, calendar.monthrange(year, month)[1])))


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + n
    return index // 12, index % 12 + 1


_AFTER = r"(?:후|뒤|있다가|지나서)"

_DATE_RULES = [
    (r"(?P<y>\d{4})[-./년](?P<m>\d{1,2})[-./월](?P<d>\d{1,2})", _absolute),
    (r"(?P<m>\d{1,2})월(?P<d>\d{1,2})일", _month_day),
    (r"(?P<m>\d{1,2})월(?P<part>초|중순|말)", _month_part),
    (r"(?<![\d.])(?P<m>\d{1,2})[/.](?P<d>\d{1,2})(?![\d.:])", _month_day),
    (rf"(?:(?P<p>{_MONTH_PREFIX})달|월)(?P<part>\d{{1,2}}일|초|중순|말)", _relative_month),
    (rf"(?:(?P<w>{'|'.join(_DAY_COUNTS)})|(?P<n>\d{{1,3}})일){_AFTER}", _after_days),
    (rf"(?:일주일|(?P<n>{NUM})주(?:일)?){_AFTER}", _after_weeks),
    (rf"(?P<n>{NUM})(?:달|개월){_AFTER}", _after_months),
    ("|".join(sorted(_DAY_WORDS, key=len, reverse=True)), _day_word),
    (rf"(?P<p>{_WEEK_PREFIX})?주말", _weekend),
    (rf"(?:(?P<p>{_WEEK_PREFIX})주)?(?P<t>[{_WEEKDAYS}])요일", _week_day),
    (rf"(?P<p>{_WEEK_PREFIX})주", _week_only),
    (r"(?<!\d)(?P<d>\d{1,2})일", _bare_day),
]


# ── 시각 ────────────────────────────────────────────────────────────────

_AM_WORDS = ("새벽", "아침", "오전")
_PM_WORDS = ("오후", "저녁", "낮")
_PERIOD = r"(?P<period>새벽|아침|오전|오후|저녁|밤|낮|점심)?"


@dataclass(frozen=True)
class Defaults:
    """시각이 없는 모호 표현의 기본값 (spec 5장 4단계, context.yaml `defaults`)."""

    dawn: time = time(6)
    morning: time = time(9)
    lunch: time = time(12)
    afternoon: time = time(14)
    evening: time = time(19)
    night: time = time(21)
    duration: timedelta = timedelta(minutes=60)

    def for_period(self, period: str) -> time | None:
        return {
            "새벽": self.dawn, "아침": self.morning, "오전": self.morning,
            "점심": self.lunch, "낮": self.afternoon, "오후": self.afternoon,
            "저녁": self.evening, "밤": self.night,
        }.get(period)


@dataclass(frozen=True)
class Clock:
    """하루 안의 시각. `day_offset`은 "밤 12시"처럼 다음 날로 넘어가는 경우 1이다."""

    value: time
    day_offset: int = 0


@dataclass(frozen=True)
class TimeResult:
    start: Clock
    end: Clock | None = None
    assumed: frozenset[str] = field(default_factory=frozenset)


_CLOCK = (
    rf"{_PERIOD}(?:(?P<hh>\d{{1,2}}):(?P<mm>\d{{2}})"
    rf"|(?P<h>{NUM})시(?!간)(?:(?P<half>반)|(?P<min>\d{{1,2}}|{SINO})분|정각)?)"
)


def parse_time(expr: str, defaults: Defaults = Defaults()) -> TimeResult | None:
    s = compact(expr)
    if s in ("정오", "낮12시"):
        return TimeResult(Clock(time(12)))
    if s == "자정":
        return TimeResult(Clock(time(0), day_offset=1))

    clocks = list(re.finditer(_CLOCK, s))
    if not clocks:
        period = re.search(r"새벽|아침|오전|오후|저녁|밤|낮|점심", s)
        if period:
            return TimeResult(Clock(defaults.for_period(period[0])), assumed=frozenset({"time"}))
        return None

    assumed: set[str] = set()
    start = _clock(clocks[0], None, assumed)
    end = None
    if len(clocks) >= 2 and re.search(r"~|-|부터|에서", s):
        end = _clock(clocks[1], clocks[0]["period"], assumed)
        end = _after(start, end)
    return TimeResult(start, end, frozenset(assumed))


def _clock(m: re.Match, inherited_period: str | None, assumed: set[str]) -> Clock:
    period = m["period"] or inherited_period
    if m["hh"]:
        hour, minute = int(m["hh"]), int(m["mm"])
    else:
        hour = to_int(m["h"])
        minute = 30 if m["half"] else (to_int(m["min"]) if m["min"] else 0)

    if hour >= 24:
        return Clock(time(hour - 24, minute), day_offset=1)
    if hour >= 13 or hour == 0:
        return Clock(time(hour, minute))
    if period in _AM_WORDS:
        return Clock(time(0 if hour == 12 else hour, minute))
    if period in _PM_WORDS:
        return Clock(time(hour if hour == 12 else hour + 12, minute))
    if period == "점심":
        return Clock(time(hour if hour >= 11 else hour + 12, minute))
    if period == "밤":
        # 밤 9~11시는 저녁, 밤 12시 이후는 다음 날 새벽이다.
        if 6 <= hour <= 11:
            return Clock(time(hour + 12, minute))
        return Clock(time(hour % 12, minute), day_offset=1)
    # 오전·오후 표시가 없으면 업무 시간으로 추정한다: 1~6시는 오후, 7~11시는 오전.
    if hour == 12:
        return Clock(time(12, minute))
    assumed.add("meridiem")
    return Clock(time(hour + 12 if hour <= 6 else hour, minute))


def _after(start: Clock, end: Clock) -> Clock:
    """범위 끝이 시작보다 이르면 12시간 뒤(오전→오후)나 다음 날로 옮긴다."""
    def minutes(c: Clock) -> int:
        return c.day_offset * 1440 + c.value.hour * 60 + c.value.minute

    if minutes(end) > minutes(start):
        return end
    if end.value.hour < 12 and minutes(end) + 720 > minutes(start):
        return Clock(time(end.value.hour + 12, end.value.minute), end.day_offset)
    return Clock(end.value, end.day_offset + 1)


# ── 기간 ────────────────────────────────────────────────────────────────

_DURATION = (
    rf"(?:(?P<h>{NUM})시간(?P<half>반)?)?"
    rf"(?:(?P<m>\d{{1,3}}|{SINO})분)?"
)


def parse_relative_time(expr: str) -> timedelta | None:
    """"한 시간 뒤", "30분 후"처럼 캡처 시각에서 떨어진 시간. 날짜 단위("3일 후")는 parse_date가 맡는다."""
    s = compact(expr)
    if not re.search(rf"(?:시간|분)(?:반)?{_AFTER}", s):
        return None
    return parse_duration(re.sub(rf"{_AFTER}.*$", "", s))


def parse_duration(expr: str) -> timedelta | None:
    s = compact(expr)
    for m in re.finditer(_DURATION, s):
        if not (m["h"] or m["m"]):
            continue
        minutes = to_int(m["h"]) * 60 if m["h"] else 0
        if m["half"]:
            minutes += 30
        if m["m"]:
            minutes += to_int(m["m"])
        return timedelta(minutes=minutes)
    return None
