"""Fail-closed push-to-talk audio gate."""

import time

from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class PTTState:
    def __init__(self, enabled: bool, timeout: float = 1.5):
        self.enabled = enabled
        self.timeout = timeout
        self._active_until = 0.0

    @property
    def active(self) -> bool:
        return self.enabled and time.monotonic() < self._active_until

    def set_active(self, active: bool) -> None:
        self._active_until = time.monotonic() + self.timeout if active else 0.0


class PTTGate(FrameProcessor):
    """Replace idle microphone frames with silence before STT and VAD."""

    def __init__(self, state: PTTState, **kwargs):
        super().__init__(**kwargs)
        self._state = state

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if (
            direction is FrameDirection.DOWNSTREAM
            and isinstance(frame, InputAudioRawFrame)
            and self._state.enabled
            and not self._state.active
        ):
            frame.audio = bytes(len(frame.audio))
        await self.push_frame(frame, direction)
