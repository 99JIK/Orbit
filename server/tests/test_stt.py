from orbit_server.eval_stt import cer
from orbit_server.stt import build_hint


def test_cer_ignores_spacing_and_punctuation():
    assert cer("다음 주 화요일, 오후 3시.", "다음주 화요일 오후 3시") == 0.0


def test_cer_counts_substitutions():
    assert cer("교수님 면담", "교수님 먼담") == 1 / 5


def test_build_hint_dedupes_and_skips_empty():
    assert build_hint(["홍길동", "", "홍길동", "카메라레디"]) == "등장인물과 용어: 홍길동, 카메라레디."
    assert build_hint([]) is None
