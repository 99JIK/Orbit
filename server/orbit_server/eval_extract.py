"""추출 품질 평가 (spec 11장, 14.1 SLM 비교).

    python -m orbit_server.eval_extract --url http://localhost:11434 --models gemma3:4b llama3.2:3b

평가 세트의 각 문장을 후보로 만들어 유형과 시작 시각을 기대값과 비교한다.
캡처 시각은 2026-11-02(월) 15:24 KST로 고정한다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .candidates import Candidate, build_candidate
from .extract import OllamaModel

CAPTURED_AT = datetime(2026, 11, 2, 15, 24, tzinfo=timezone(timedelta(hours=9)))
DEFAULT_CASES = Path(__file__).resolve().parents[1] / "tests" / "data" / "extract_cases.jsonl"


def when_matches(candidate: Candidate, case: dict) -> bool:
    expected = case.get("start")
    when = candidate.when
    if expected is None:
        return when is None or candidate.kind in ("task", "note", "ignore")
    if when is None:
        return False
    if "T" in expected:
        ok = not when.all_day and when.start.strftime("%Y-%m-%dT%H:%M") == expected
    else:
        ok = when.start.date().isoformat() == expected
    if ok and case.get("end"):
        ok = when.end.strftime("%Y-%m-%dT%H:%M") == case["end"]
    return ok


def describe(candidate: Candidate) -> str:
    if candidate.when is None:
        return "-"
    if candidate.when.all_day:
        return candidate.when.start.strftime("%m/%d 종일")
    text = candidate.when.start.strftime("%m/%d %H:%M")
    if candidate.when.assumed:
        text += " (" + ",".join(sorted(candidate.when.assumed)) + ")"
    return text


def evaluate(url: str, model_name: str, cases: list[dict]) -> dict:
    model = OllamaModel(url, model_name)
    kind_ok = when_ok = failures = 0
    latencies = []
    print(f"\n## {model_name}\n")
    print("| # | 판정 | 시각 | 유형 | 제목 | 해석 | 원문 표현 |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for i, case in enumerate(cases, 1):
        try:
            cand = build_candidate(case["text"], CAPTURED_AT, model)
        except Exception as e:  # 평가 도구이므로 한 건 실패가 전체를 멈추지 않게 한다
            failures += 1
            print(f"| {i} | ✗ | ✗ | 오류 | {type(e).__name__}: {e} | | |")
            continue
        latencies.append(cand.extraction.latency_s)
        k = cand.kind in case["kind"]
        w = when_matches(cand, case)
        kind_ok += k
        when_ok += w
        ex = cand.extraction
        exprs = " / ".join(x for x in (ex.date_expr, ex.time_expr, ex.duration_expr) if x) or "-"
        kind = cand.kind + (" (강등)" if cand.demoted else "")
        print(f"| {i} | {'✓' if k else '✗'} | {'✓' if w else '✗'} | {kind} | {cand.title} | {describe(cand)} | {exprs} |")

    n = len(cases)
    summary = {
        "model": model_name,
        "kind_acc": kind_ok / n,
        "when_acc": when_ok / n,
        "errors": failures,
        "median_latency_s": statistics.median(latencies) if latencies else None,
    }
    print(f"\n유형 {kind_ok}/{n} · 시각 {when_ok}/{n} · 오류 {failures} · 지연 중앙값 {summary['median_latency_s']}s")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:11434")
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = [evaluate(args.url, m, cases) for m in args.models]

    print("\n## 요약\n")
    print("| 모델 | 유형 정확도 | 시각 정확도 | 오류 | 지연 중앙값 |")
    print("| --- | --- | --- | --- | --- |")
    for r in results:
        print(f"| {r['model']} | {r['kind_acc']:.0%} | {r['when_acc']:.0%} | {r['errors']} | {r['median_latency_s']}s |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
