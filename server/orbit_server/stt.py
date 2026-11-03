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
            if self.device in ("auto", "cuda"):
                _preload_cuda_libs()
            from faster_whisper import WhisperModel  # 선택 의존성: pip install orbit-server[stt]

            self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)

    def transcribe(self, path: Path, hint: str | None = None) -> Transcript:
        self.load()
        started = _time.perf_counter()
        segments, info = self._model.transcribe(
            decode_audio(path),
            language="ko",
            initial_prompt=hint,
            vad_filter=True,
            beam_size=5,
            # 통화는 앞 문맥을 이어 받으면 같은 문장을 반복 생성하는 환각이 잦다
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return Transcript(text, info.language, round(info.duration, 1), round(_time.perf_counter() - started, 2))

    def unload(self) -> None:
        self._model = None
        gc.collect()


def decode_audio(path: Path, sr: int = 16000):
    """16kHz 모노 float32 파형.

    faster-whisper 1.2.1의 내장 decode_audio는 PyAV 19에서 없어진 `metadata_errors` 인자를 넘겨 실패한다.
    PyAV 공개 API만 써서 직접 디코딩하고 파형을 넘긴다.
    """
    import av
    import numpy as np

    chunks = []
    with av.open(str(path), mode="r") as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sr)
        for frame in container.decode(audio=0):
            chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
        chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def _preload_cuda_libs() -> None:
    """pip `nvidia-*-cu12` 휠의 cuBLAS·cuDNN을 절대 경로로 먼저 올린다.

    CTranslate2는 soname으로 dlopen하는데, 휠은 라이브러리를 site-packages 안에 두고 LD_LIBRARY_PATH는
    프로세스 시작 때만 읽힌다. 시스템에 CUDA가 설치돼 있으면 아무 일도 하지 않는 것과 같다.
    """
    import ctypes
    import glob
    import sys

    for sp in sys.path:
        base = Path(sp) / "nvidia"
        if not base.is_dir():
            continue
        for pattern in ("cublas/lib/libcublasLt.so.*", "cublas/lib/libcublas.so.*", "cudnn/lib/libcudnn*.so.*"):
            for so in sorted(glob.glob(str(base / pattern))):
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def build_hint(names: list[str]) -> str | None:
    """고유명사 힌트. whisper는 initial prompt를 직전 발화처럼 다루므로 짧은 문장 형태로 만든다."""
    names = [n for n in dict.fromkeys(names) if n]
    return ("등장인물과 용어: " + ", ".join(names) + ".") if names else None
