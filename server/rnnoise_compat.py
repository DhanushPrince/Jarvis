"""Compat for pipecat RNNoiseFilter + pyrnnoise/audiolab API drift.

pyrnnoise 0.4.x constructs audiolab.av.Graph(rate=...), while audiolab 0.5+
renamed that kwarg to sample_rate. Import this module once before RNNoiseFilter.
"""
from __future__ import annotations

_patched = False


def ensure_rnnoise_compat() -> None:
    global _patched
    if _patched:
        return
    try:
        from audiolab.av.graph import Graph
    except Exception:
        _patched = True
        return

    import inspect

    sig = inspect.signature(Graph.__init__)
    if "rate" in sig.parameters:
        _patched = True
        return

    _orig = Graph.__init__

    def _init(self, *args, rate=None, sample_rate=None, **kwargs):
        if sample_rate is None and rate is not None:
            sample_rate = rate
        return _orig(self, *args, sample_rate=sample_rate, **kwargs)

    Graph.__init__ = _init  # type: ignore[method-assign]
    _patched = True
