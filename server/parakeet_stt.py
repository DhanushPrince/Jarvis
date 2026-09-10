"""Parakeet-MLX STT service for Pipecat (Apple Silicon, fully local).

A thin SegmentedSTTService wrapper around senstella/parakeet-mlx so you can
use NVIDIA Parakeet TDT (ported to MLX) as a drop-in Pipecat STT on 8GB Macs.

Why this exists:
    Pipecat ships WhisperSTTServiceMLX but no Parakeet service. Parakeet-v3-int8
    is smaller/faster than Whisper-large and about as accurate, and its peak
    memory (~1.27GB) fits comfortably beside Qwen3-0.6B-4bit + Kokoro on 8GB.

Install:
    pip install parakeet-mlx

Use (see bot.py):
    STT_ENGINE=parakeet PARAKEET_MODEL=sonic-speech/parakeet-tdt-0.6b-v3-int8

Interface notes (verified against pipecat main, services/stt_service.py):
    - SegmentedSTTService buffers audio while the user speaks and calls
      run_stt(audio: bytes) once, after UserStoppedSpeaking, with a full
      WAV-framed PCM segment (16kHz mono s16le for this transport).
    - run_stt must be an async generator yielding TranscriptionFrame / ErrorFrame.
    - parakeet-mlx is synchronous + CPU/GPU bound, so we run it in a thread
      executor to avoid blocking the pipeline event loop.
"""

from __future__ import annotations

import asyncio
import io
import wave
from collections.abc import AsyncGenerator

import numpy as np
from loguru import logger

from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601


class ParakeetMLXSTTService(SegmentedSTTService):
    """Local Parakeet STT via parakeet-mlx, English-only usage here."""

    def __init__(
        self,
        *,
        model: str = "sonic-speech/parakeet-tdt-0.6b-v3-int8",
        sample_rate: int = 16000,
        **kwargs,
    ):
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._model_name = model
        self._model = None  # lazy-loaded on first segment

    def can_generate_metrics(self) -> bool:
        return True

    def _ensure_model(self):
        if self._model is None:
            logger.info(f"Loading Parakeet MLX model: {self._model_name}")
            from parakeet_mlx import from_pretrained

            self._model = from_pretrained(self._model_name)
            logger.info("Parakeet MLX model loaded.")
        return self._model

    @staticmethod
    def _pcm_from_wav_bytes(audio: bytes) -> tuple[np.ndarray, int]:
        """Decode the WAV segment Pipecat hands us into float32 mono PCM."""
        with wave.open(io.BytesIO(audio), "rb") as wf:
            sr = wf.getframerate()
            n = wf.getnframes()
            raw = wf.readframes(n)
        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm, sr

    def _transcribe_sync(self, audio: bytes) -> str:
        model = self._ensure_model()
        pcm, sr = self._pcm_from_wav_bytes(audio)
        # parakeet-mlx accepts a numpy float32 array + sample rate.
        result = model.transcribe(pcm, sr)
        return (getattr(result, "text", None) or str(result)).strip()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        try:
            await self.start_processing_metrics()
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(None, self._transcribe_sync, audio)
            await self.stop_processing_metrics()

            if not text:
                logger.warning("Parakeet returned empty transcription")
                return

            logger.debug(f"Transcription (parakeet): [{text}]")
            yield TranscriptionFrame(
                text,
                self._user_id,
                time_now_iso8601(),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Parakeet STT failed")
            yield ErrorFrame(error=f"Parakeet STT error: {e}")
