"""macOS global hold-to-talk listener, independent of agent PTT."""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

KEYCODES = {
    **dict(zip("asdfhgzxcvbnmkjl", (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 45, 46, 40, 38, 37))),
    **dict(zip("qwertyuiop", (12, 13, 14, 15, 17, 16, 32, 34, 31, 35))),
    **dict(zip("1234567890", (18, 19, 20, 21, 23, 22, 26, 28, 25, 29))),
    "return": 36,
    "tab": 48,
    "space": 49,
    "delete": 51,
    "escape": 53,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
}
MODIFIERS = {"command": 1 << 20, "shift": 1 << 17, "control": 1 << 18, "option": 1 << 19}
ALIASES = {"cmd": "command", "ctrl": "control", "alt": "option", "esc": "escape"}


@dataclass(frozen=True)
class Hotkey:
    keycode: int
    modifiers: int = 0
    modifier_only: bool = False


def parse_hotkey(value: str) -> Hotkey:
    value = value.strip().lower()
    if value in {"right_command", "left_command"}:
        return Hotkey(54 if value == "right_command" else 55, modifier_only=True)
    parts = [ALIASES.get(part.strip(), part.strip()) for part in value.split("+")]
    keys = [part for part in parts if part not in MODIFIERS]
    if len(keys) != 1 or keys[0] not in KEYCODES:
        raise ValueError("use right_command, left_command, or a chord such as control+space")
    unknown = set(parts) - set(MODIFIERS) - set(keys)
    if unknown:
        raise ValueError(f"unknown hotkey part(s): {', '.join(sorted(unknown))}")
    modifiers = 0
    for part in parts:
        modifiers |= MODIFIERS.get(part, 0)
    return Hotkey(KEYCODES[keys[0]], modifiers)


class QuartzHotkeyListener:
    def __init__(
        self,
        hotkey: Hotkey,
        on_down: Callable[[], None],
        on_up: Callable[[], None],
        on_cancel: Callable[[], None],
        on_health: Callable[[], None],
    ):
        self.hotkey = hotkey
        self.on_down = on_down
        self.on_up = on_up
        self.on_cancel = on_cancel
        self.on_health = on_health
        self.tap = None
        self.source = None
        self._pressed = False
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None

    def install(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("system-wide dictation is supported only on macOS")
        import Quartz

        def callback(proxy, event_type, event, refcon):
            self.on_health()
            if event_type in {
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            }:
                self._pressed = False
                self.on_cancel()
                Quartz.CGEventTapEnable(self.tap, True)
                return event

            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            if event_type == Quartz.kCGEventKeyDown and keycode == KEYCODES["escape"]:
                self._pressed = False
                self.on_cancel()
                return event

            active = self._pressed
            if (
                self.hotkey.modifier_only
                and event_type == Quartz.kCGEventFlagsChanged
                and keycode == self.hotkey.keycode
            ):
                active = bool(
                    Quartz.CGEventSourceKeyState(
                        Quartz.kCGEventSourceStateCombinedSessionState,
                        self.hotkey.keycode,
                    )
                )
            elif not self.hotkey.modifier_only:
                flags = Quartz.CGEventGetFlags(event)
                if event_type == Quartz.kCGEventKeyDown and keycode == self.hotkey.keycode:
                    active = (flags & self.hotkey.modifiers) == self.hotkey.modifiers
                elif event_type == Quartz.kCGEventKeyUp and keycode == self.hotkey.keycode:
                    active = False
                elif event_type == Quartz.kCGEventFlagsChanged and self._pressed:
                    active = (flags & self.hotkey.modifiers) == self.hotkey.modifiers

            if active != self._pressed:
                self._pressed = active
                (self.on_down if active else self.on_up)()
            return event

        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
        )
        self.tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            callback,
            None,
        )
        if self.tap is None:
            raise PermissionError(
                "macOS blocked the event tap; grant Accessibility and Input Monitoring"
            )
        self.source = Quartz.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetMain(),
            self.source,
            Quartz.kCFRunLoopCommonModes,
        )
        Quartz.CGEventTapEnable(self.tap, True)
        self._monitor = threading.Thread(target=self._monitor_tap, daemon=True)
        self._monitor.start()

    def _monitor_tap(self) -> None:
        import Quartz

        while not self._stop.wait(0.2):
            if self.tap and Quartz.CGEventTapIsEnabled(self.tap):
                self.on_health()
            else:
                self._pressed = False
                self.on_cancel()
                if self.tap:
                    Quartz.CGEventTapEnable(self.tap, True)

    def close(self) -> None:
        self._stop.set()
        self.on_cancel()
        if self._monitor:
            self._monitor.join(timeout=0.5)

