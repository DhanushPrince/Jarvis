"""Smoke test: RNNoiseFilter processes PCM and reduces synthetic noise energy.

Run: python test_rnnoise_filter.py
Requires: pipecat-ai[rnnoise] (pyrnnoise + soxr)
"""
from __future__ import annotations

import asyncio
import math
import struct
import sys


def _rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    n = len(pcm) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    return math.sqrt(sum(s * s for s in samples) / n)


def _tone_plus_noise(sr: int, seconds: float, tone_hz: float = 440.0, noise_amp: float = 0.35) -> bytes:
    import random

    n = int(sr * seconds)
    out = bytearray()
    for i in range(n):
        t = i / sr
        sig = 0.25 * math.sin(2 * math.pi * tone_hz * t)
        noise = noise_amp * (random.random() * 2 - 1)
        sample = max(-1.0, min(1.0, sig + noise))
        out += struct.pack("<h", int(sample * 32767))
    return bytes(out)


async def main() -> int:
    from rnnoise_compat import ensure_rnnoise_compat
    ensure_rnnoise_compat()
    from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter

    sr = 16000
    filt = RNNoiseFilter()
    await filt.start(sr)
    if not getattr(filt, "_rnnoise_ready", False):
        print("FAIL: RNNoise did not initialize (install pipecat-ai[rnnoise])")
        return 1

    raw = _tone_plus_noise(sr, 1.0)
    chunk = int(sr * 0.02) * 2
    cleaned = bytearray()
    for i in range(0, len(raw), chunk):
        piece = raw[i : i + chunk]
        out = await filt.filter(piece)
        if out:
            cleaned.extend(out)

    await filt.stop()

    in_rms = _rms(raw)
    out_rms = _rms(bytes(cleaned))
    print(f"sample_rate={sr}")
    print(f"input_bytes={len(raw)} output_bytes={len(cleaned)}")
    print(f"input_rms={in_rms:.1f} output_rms={out_rms:.1f}")

    if len(cleaned) < len(raw) // 4:
        print("FAIL: filter produced too little audio")
        return 1
    ratio = out_rms / in_rms if in_rms else 0
    print(f"rms_ratio={ratio:.3f}")
    print("PASS: RNNoiseFilter processed PCM successfully")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
