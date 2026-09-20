"""16 kHz mono microphone capture."""

from __future__ import annotations

import threading

import numpy as np


class MicrophoneRecorder:
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd

        if self._stream is not None:
            return
        with self._lock:
            self._chunks.clear()

        def callback(indata, frames, time_info, status):
            if status:
                print(f"Microphone warning: {status}")
            with self._lock:
                self._chunks.append(indata[:, 0].copy())

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        if self._stream is None:
            return np.empty(0, dtype=np.float32)
        stream, self._stream = self._stream, None
        stream.stop()
        stream.close()
        with self._lock:
            chunks, self._chunks = self._chunks, []
        return np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)

    def cancel(self) -> None:
        self.stop()

