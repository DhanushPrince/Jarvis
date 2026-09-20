"""Local STT and optional local transcript cleanup."""

from __future__ import annotations

from typing import Protocol

import numpy as np


class SpeechToText(Protocol):
    def transcribe(self, samples: np.ndarray, sample_rate: int) -> str: ...


class TranscriptCleanup(Protocol):
    def clean(self, text: str) -> str: ...


class ParakeetMLX:
    """Lazy-loaded, fully local Parakeet-MLX backend."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> str:
        if self._model is None:
            from parakeet_mlx import from_pretrained

            self._model = from_pretrained(self.model_name)
        result = self._model.transcribe(samples.astype(np.float32, copy=False), sample_rate)
        return (getattr(result, "text", None) or str(result)).strip()


class NoCleanup:
    def clean(self, text: str) -> str:
        return text


class S1MiniCleanup:
    """Run a user-installed s1-mini GGUF through llama.cpp, entirely on-device."""

    def __init__(self, model_path: str):
        from llama_cpp import Llama

        self._llm = Llama(model_path=model_path, n_ctx=2048, verbose=False)

    def clean(self, text: str) -> str:
        prompt = (
            "Clean this English speech transcript. Preserve meaning and wording; "
            "only fix punctuation, capitalization, obvious recognition errors, and "
            "remove filler words. Return only the cleaned transcript.\n\n"
            f"Transcript: {text}\nClean transcript:"
        )
        result = self._llm(
            prompt,
            max_tokens=min(512, max(64, len(text))),
            temperature=0,
            stop=["\n\n"],
        )
        cleaned = result["choices"][0]["text"].strip()
        return cleaned or text


def make_stt(backend: str, model_name: str) -> SpeechToText:
    if backend.lower() != "parakeet":
        raise ValueError("Python dictation MVP currently supports stt_backend: parakeet")
    return ParakeetMLX(model_name)


def make_cleanup(enabled: bool, backend: str, model_path: str) -> TranscriptCleanup:
    if not enabled:
        return NoCleanup()
    if backend.lower() != "s1-mini":
        raise ValueError("cleanup_backend must be s1-mini")
    return S1MiniCleanup(model_path)

