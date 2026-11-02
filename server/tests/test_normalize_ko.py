"""한국어 날짜 정규화. 기준 시각은 2026-11-02(월) 15:24 KST."""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from orbit_server.locales.ko import parse_date, parse_duration, parse_time
from orbit_server.normalize import resolve

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 2, 15, 24, tzinfo=KST)
TODAY = NOW.date()


@pytest.mark.parametrize("expr, expected, assumed", [
    # 날짜 단어
    ("오늘", date(2026, 11, 2), False),
    ("내일", date(2026, 11, 3), False),
    ("모레", date(2026, 11, 4), False),
    ("내일 모레", date(2026, 11, 4), False),
    ("글피", date(2026, 11, 5), False),
    ("어제", date(2026, 11, 1), False),
    # 주 단위 (주는 월요일 시작, 기준일은 월요일)
    ("다음 주 화요일", date(2026, 11, 10), False),
    ("다음주 화요일에", date(2026, 11, 10), False),
    ("담주 금요일", date(2026, 11, 13), False),
    ("이번 주 금요일까지", date(2026, 11, 6), False),
    ("금주 수요일", date(2026, 11, 4), False),
    ("다다음 주 월요일", date(2026, 11, 16), False),
    ("지난주 목요일", date(2026, 10, 29), False),
    ("화요일", date(2026, 11, 3), False),
    ("월요일", date(2026, 11, 9), False),      # 오늘과 같은 요일이면 다음 주
    ("일요일", date(2026, 11, 8), False),
    ("이번 주말", date(2026, 11, 7), True),
    ("다음 주말", date(2026, 11, 14), True),
    ("주말", date(2026, 11, 7), True),
    ("다음 주", date(2026, 11, 13), True),     # 요일 없으면 그 주 금요일 추정
    ("차주", date(2026, 11, 13), True),
    # 상대 기간
    ("3일 후", date(2026, 11, 5), False),
    ("이틀 뒤", date(2026, 11, 4), False),
    ("열흘 뒤", date(2026, 11, 12), False),
    ("보름 후", date(2026, 11, 17), False),
    ("일주일 뒤", date(2026, 11, 9), False),
    ("2주 후", date(2026, 11, 16), False),
    ("두 주 뒤", date(2026, 11, 16), False),
    ("한 달 뒤", date(2026, 12, 2), False),
    ("3개월 후", date(2027, 2, 2), False),
    # 절대 날짜
    ("11월 10일", date(2026, 11, 10), False),
    ("12월 25일", date(2026, 12, 25), False),
    ("1월 3일", date(2027, 1, 3), False),      # 지난 날짜는 내년
    ("10월 30일", date(2026, 10, 30), False),  # 일주일 이내 과거는 그대로 (검증 단계가 거름)
    ("11/20", date(2026, 11, 20), False),
    ("2026-12-01", date(2026, 12, 1), False),
    ("2027년 3월 2일", date(2027, 3, 2), False),
    ("15일", date(2026, 11, 15), False),
    ("1일", date(2026, 12, 1), False),         # 이번 달 1일은 지났으므로 다음 달
    # 월 단위
    ("다음 달 5일", date(2026, 12, 5), False),
    ("다음달 말", date(2026, 12, 31), True),
    ("이번 달 말", date(2026, 11, 30), True),
    ("월말", date(2026, 11, 30), True),
    ("다음 달 초", date(2026, 12, 1), True),
    ("12월 말", date(2026, 12, 31), True),
    ("2월 중순", date(2027, 2, 15), True),
])
def test_parse_date(expr, expected, assumed):
    result = parse_date(expr, TODAY)
    assert result is not None, expr
    assert (result.value, result.assumed) == (expected, assumed)


@pytest.mark.parametrize("expr", ["언젠가", "나중에", "조만간", ""])
def test_parse_date_unresolvable(expr):
    assert parse_date(expr, TODAY) is None


@pytest.mark.parametrize("expr, start, day_offset, assumed", [
    ("오후 3시", time(15), 0, set()),
    ("오후 3시 반", time(15, 30), 0, set()),
    ("오전 10시 20분", time(10, 20), 0, set()),
    ("오전 12시", time(0), 0, set()),
    ("저녁 7시", time(19), 0, set()),
    ("아침 8시", time(8), 0, set()),
    ("새벽 2시", time(2), 0, set()),
    ("점심 1시", time(13), 0, set()),
    ("낮 2시", time(14), 0, set()),
    ("밤 11시", time(23), 0, set()),
    ("밤 12시", time(0), 1, set()),
    ("밤 1시", time(1), 1, set()),
    ("15시", time(15), 0, set()),
    ("15:30", time(15, 30), 0, set()),
    ("오후 2:30", time(14, 30), 0, set()),
    ("세 시", time(15), 0, {"meridiem"}),       # 표시 없음: 1~6시는 오후
    ("열한시", time(11), 0, {"meridiem"}),       # 7~11시는 오전
    ("12시", time(12), 0, set()),
    ("3시 삼십분", time(15, 30), 0, {"meridiem"}),
    ("정오", time(12), 0, set()),
    ("자정", time(0), 1, set()),
    ("오후", time(14), 0, {"time"}),            # 시각 없는 모호 표현은 기본값
    ("점심", time(12), 0, {"time"}),
    ("저녁에", time(19), 0, {"time"}),
    ("아침", time(9), 0, {"time"}),
])
def test_parse_time(expr, start, day_offset, assumed):
    result = parse_time(expr)
    assert result is not None, expr
    assert (result.start.value, result.start.day_offset, set(result.assumed)) == (start, day_offset, assumed)


@pytest.mark.parametrize("expr, start, end", [
    ("오후 3시~5시", time(15), time(17)),
    ("3시부터 5시까지", time(15), time(17)),
    ("오전 10시부터 오후 2시까지", time(10), time(14)),
    ("저녁 7시에서 9시", time(19), time(21)),
    ("11시~1시", time(11), time(13)),
])
def test_parse_time_range(expr, start, end):
    result = parse_time(expr)
    assert (result.start.value, result.end.value) == (start, end)


def test_parse_time_ignores_duration_words():
    assert parse_time("한 시간") is None
    assert parse_time("세시간") is None


@pytest.mark.parametrize("expr, minutes", [
    ("1시간", 60),
    ("한 시간", 60),
    ("한시간 반", 90),
    ("두 시간 반", 150),
    ("1시간 30분", 90),
    ("90분", 90),
    ("삼십 분", 30),
])
def test_parse_duration(expr, minutes):
    assert parse_duration(expr) == timedelta(minutes=minutes)


# ── resolve: 파이프라인 진입점 ──────────────────────────────────────────

def at(*args):
    return datetime(*args, tzinfo=KST)


def test_spec_example():
    """spec 5장 3단계 예시: 다음 주 화요일 오후 3시, 기간 미지정."""
    r = resolve("다음 주 화요일", "오후 3시", None, NOW)
    assert (r.start, r.end, r.all_day) == (at(2026, 11, 10, 15), at(2026, 11, 10, 16), False)
    assert r.assumed == {"duration"}


def test_explicit_duration():
    r = resolve("내일", "오전 10시", "1시간 30분", NOW)
    assert (r.start, r.end, r.assumed) == (at(2026, 11, 3, 10), at(2026, 11, 3, 11, 30), frozenset())


def test_time_range_sets_end():
    r = resolve("금요일", "오후 2시부터 4시까지", None, NOW)
    assert (r.start, r.end, r.assumed) == (at(2026, 11, 6, 14), at(2026, 11, 6, 16), frozenset())


def test_range_end_in_duration_expr():
    """SLM이 범위의 끝을 duration_expr에 넣은 경우."""
    r = resolve("다음 주 수요일", "세 시", "다섯 시까지", NOW)
    assert (r.start, r.end) == (at(2026, 11, 11, 15), at(2026, 11, 11, 17))


def test_date_only_is_all_day():
    r = resolve("11월 20일", None, None, NOW)
    assert (r.start, r.end, r.all_day) == (at(2026, 11, 20), at(2026, 11, 21), True)


def test_vague_time_uses_defaults():
    r = resolve("내일", "점심", None, NOW)
    assert r.start == at(2026, 11, 3, 12)
    assert r.assumed == {"time", "duration"}


def test_time_only_today_or_tomorrow():
    later = resolve(None, "오후 5시", None, NOW)
    assert later.start == at(2026, 11, 2, 17) and "date" in later.assumed
    earlier = resolve(None, "오전 9시", None, NOW)
    assert earlier.start == at(2026, 11, 3, 9)


def test_time_inside_date_expr():
    """SLM이 시각을 date_expr에 섞어 낸 경우."""
    r = resolve("내일 오후 3시", None, None, NOW)
    assert r.start == at(2026, 11, 3, 15) and not r.all_day


@pytest.mark.parametrize("expr, minutes", [
    ("한 시간 뒤", 60),
    ("30분 후", 30),
    ("두 시간 반 있다가", 150),
])
def test_relative_time_from_capture(expr, minutes):
    r = resolve(None, expr, None, NOW)
    assert r.start == NOW + timedelta(minutes=minutes)
    assert resolve(expr, None, None, NOW).start == r.start


def test_late_night_rolls_to_next_day():
    r = resolve("금요일", "밤 12시", None, NOW)
    assert r.start == at(2026, 11, 7, 0)


def test_week_only_marks_assumed_date():
    r = resolve("다음 주", "오후 3시", None, NOW)
    assert r.start == at(2026, 11, 13, 15) and "date" in r.assumed


@pytest.mark.parametrize("date_expr, time_expr", [
    (None, None),
    ("언젠가", "오후 3시"),
    (None, "적당할 때"),
])
def test_unresolvable_returns_none(date_expr, time_expr):
    assert resolve(date_expr, time_expr, None, NOW) is None


def test_requires_aware_captured_at():
    with pytest.raises(ValueError):
        resolve("내일", None, None, datetime(2026, 11, 2, 15, 24))


def test_relative_to_capture_not_processing_time():
    """같은 표현도 캡처 시각에 따라 다른 날이 된다 (처리가 늦어도 결과가 바뀌지 않음)."""
    friday = datetime(2026, 11, 6, 22, 0, tzinfo=KST)
    assert resolve("내일", "오후 3시", None, friday).start == at(2026, 11, 7, 15)
