"""STT (spec 5장 1단계). Ollama는 음성을 다루지 않으므로 whisper 계열 엔진을 따로 쓴다.

모델은 메모리 예산(spec 4.3) 때문에 상주시키지 않는다. 작업 단위로 `load()` → `transcribe()` → `unload()` 한다.
"""

from __future__ import annotations

import gc
import time as _time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    audio_s: float
    latency_s: float


class Transcriber(Protocol):
    def load(self) -> None: ...
    def transcribe(self, path: Path, hint: str | None = None) -> Transcript: ...
    def unload(self) -> None: ...


class FasterWhisper:
    """faster-whisper (CTranslate2). NVIDIA GPU면 `device="cuda"`, 그 외에는 CPU int8.

    `hint`는 맥락 사전의 고유명사와 통화 상대 이름이다. whisper의 initial prompt로 넣어 인식률을 높인다.
    """

    def __init__(self, model: str = "large-v3-turbo", device: str = "auto", compute_type: str = "auto"):
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def load(self) -> None:
        if self._model is None:
            from faster_whisper import WhisperModel  # 선택 의존성: pip install orbit-server[stt]

            self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)

    def transcribe(self, path: Path, hint: str | None = None) -> Transcript:
        self.load()
        started = _time.perf_counter()
        segments, info = self._model.transcribe(
            str(path),
            language="ko",
            initial_prompt=hint,
            vad_filter=True,
            beam_size=5,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return Transcript(text, info.language, round(info.duration, 1), round(_time.perf_counter() - started, 2))

    def unload(self) -> None:
        self._model = None
        gc.collect()


def build_hint(names: list[str]) -> str | None:
    """고유명사 힌트. whisper는 initial prompt를 직전 발화처럼 다루므로 짧은 문장 형태로 만든다."""
    names = [n for n in dict.fromkeys(names) if n]
    return ("등장인물과 용어: " + ", ".join(names) + ".") if names else None
