"""Pure hold-to-talk state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class State(Enum):
    IDLE = auto()
    RECORDING = auto()
    PROCESSING = auto()
    ERROR = auto()


class Action(Enum):
    NONE = auto()
    START_CAPTURE = auto()
    PROCESS_CAPTURE = auto()
    CANCEL_CAPTURE = auto()


@dataclass
class DictationSession:
    fail_closed_seconds: float = 1.5
    state: State = State.IDLE
    last_listener_health: float = 0.0

    def listener_healthy(self, now: float) -> None:
        self.last_listener_health = now

    def hotkey_down(self, now: float) -> Action:
        if self.state is State.ERROR:
            self.state = State.IDLE
        if self.state is not State.IDLE:
            return Action.NONE
        self.state = State.RECORDING
        self.last_listener_health = now
        return Action.START_CAPTURE

    def hotkey_up(self) -> Action:
        if self.state is not State.RECORDING:
            return Action.NONE
        self.state = State.PROCESSING
        return Action.PROCESS_CAPTURE

    def cancel(self) -> Action:
        if self.state is not State.RECORDING:
            return Action.NONE
        self.state = State.IDLE
        return Action.CANCEL_CAPTURE

    def tick(self, now: float) -> Action:
        if (
            self.state is State.RECORDING
            and now - self.last_listener_health >= self.fail_closed_seconds
        ):
            return self.cancel()
        return Action.NONE

    def processing_finished(self, error: bool = False) -> None:
        if self.state is State.PROCESSING:
            self.state = State.ERROR if error else State.IDLE

    def clear_error(self) -> None:
        if self.state is State.ERROR:
            self.state = State.IDLE

