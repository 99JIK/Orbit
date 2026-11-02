"""STT 품질과 음성→후보 전체 흐름 평가 (spec 14.1).

    python -m orbit_server.eval_stt --audio-dir <dir> [--whisper large-v3-turbo] [--ollama URL --llm gemma3:4b]

`<dir>/manifest.jsonl`의 각 줄은 {"file": "a.wav", "text": "정답 문장", "case": 평가 세트 번호(선택)}이다.
`case`가 있고 --ollama를 주면 받아쓴 문장으로 후보를 만들어 eval_extract 기대값과 비교한다.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

from .candidates import build_candidate
from .eval_extract import CAPTURED_AT, DEFAULT_CASES, describe, when_matches
from .extract import OllamaModel
from .stt import FasterWhisper


def cer(reference: str, hypothesis: str) -> float:
    """글자 오류율. 공백과 문장부호는 비교에서 뺀다."""
    ref, hyp = _chars(reference), _chars(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def _chars(text: str) -> str:
    return re.sub(r"[\s\.,!?~·\-\[\]\"']", "", text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--whisper", default="large-v3-turbo")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--ollama")
    parser.add_argument("--llm", default="gemma3:4b")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    manifest = [json.loads(l) for l in (args.audio_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    cases = [json.loads(l) for l in DEFAULT_CASES.read_text(encoding="utf-8").splitlines() if l.strip()]
    whisper = FasterWhisper(args.whisper, device=args.device)
    model = OllamaModel(args.ollama, args.llm) if args.ollama else None

    print(f"## STT {args.whisper}" + (f" → {args.llm}" if model else "") + "\n")
    print("| 파일 | 길이 | 처리 | CER | 받아쓴 문장 | 후보 |")
    print("| --- | --- | --- | --- | --- | --- |")
    whisper.load()
    rates, rtf, pipeline_ok = [], [], 0
    for item in manifest:
        t = whisper.transcribe(args.audio_dir / item["file"])
        rate = cer(item["text"], t.text)
        rates.append(rate)
        rtf.append(t.latency_s / t.audio_s if t.audio_s else 0)
        result = ""
        if model and item.get("case"):
            case = cases[item["case"] - 1]
            cand = build_candidate(t.text, CAPTURED_AT, model)
            ok = cand.kind in case["kind"] and when_matches(cand, case)
            pipeline_ok += ok
            result = f"{'✓' if ok else '✗'} {cand.kind} {describe(cand)}"
        print(f"| {item['file']} | {t.audio_s}s | {t.latency_s}s | {rate:.1%} | {t.text} | {result} |")
    whisper.unload()

    n = len(manifest)
    print(f"\nCER 평균 {statistics.mean(rates):.1%} · 실시간 배율 중앙값 {statistics.median(rtf):.2f}x"
          + (f" · 음성→후보 정답 {pipeline_ok}/{n}" if model else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
