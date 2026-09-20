import sys
import unittest
from enum import Enum
from types import ModuleType


class Frame:
    pass


class InputAudioRawFrame(Frame):
    def __init__(self, audio):
        self.audio = audio


class FrameDirection(Enum):
    DOWNSTREAM = 1
    UPSTREAM = 2


class FrameProcessor:
    async def process_frame(self, frame, direction):
        pass

    async def push_frame(self, frame, direction):
        self.pushed = (frame, direction)


frames = ModuleType("pipecat.frames.frames")
frames.Frame = Frame
frames.InputAudioRawFrame = InputAudioRawFrame
processor = ModuleType("pipecat.processors.frame_processor")
processor.FrameDirection = FrameDirection
processor.FrameProcessor = FrameProcessor
sys.modules.setdefault("pipecat", ModuleType("pipecat"))
sys.modules.setdefault("pipecat.frames", ModuleType("pipecat.frames"))
sys.modules.setdefault("pipecat.processors", ModuleType("pipecat.processors"))
sys.modules["pipecat.frames.frames"] = frames
sys.modules["pipecat.processors.frame_processor"] = processor

from ptt import PTTGate, PTTState


class PTTStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_idle_audio_is_silenced_and_active_audio_passes(self):
        state = PTTState(enabled=True)
        gate = PTTGate(state)
        frame = InputAudioRawFrame(b"\x01\x02")
        await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertEqual(frame.audio, b"\x00\x00")

        state.set_active(True)
        frame = InputAudioRawFrame(b"\x01\x02")
        await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertEqual(frame.audio, b"\x01\x02")

        state.set_active(False)
        self.assertFalse(state.active)


if __name__ == "__main__":
    unittest.main()
