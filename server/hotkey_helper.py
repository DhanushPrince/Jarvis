#!/usr/bin/env python3
"""Native macOS global press-and-hold listener for Jarvis."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass

from config import CONFIG


KEYCODES = {
    **dict(zip("asdfhgzxcvbnmkjl;'\\", (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 45, 46, 40, 38, 37, 41, 39, 42))),
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

MODIFIERS = {
    "command": 1 << 20,
    "shift": 1 << 17,
    "control": 1 << 18,
    "option": 1 << 19,
}

ALIASES = {"cmd": "command", "ctrl": "control", "alt": "option", "esc": "escape"}


@dataclass(frozen=True)
class Hotkey:
    keycode: int
    modifiers: int = 0
    modifier_only: bool = False


def parse_hotkey(value: str) -> Hotkey:
    value = value.strip().lower()
    if value in ("right_command", "left_command"):
        return Hotkey(54 if value == "right_command" else 55, modifier_only=True)

    parts = [ALIASES.get(part.strip(), part.strip()) for part in value.split("+")]
    keys = [part for part in parts if part not in MODIFIERS]
    if len(keys) != 1 or keys[0] not in KEYCODES:
        raise ValueError(
            "hotkey.key must be right_command, left_command, or a chord such as command+space"
        )
    modifiers = 0
    for part in parts:
        if part in MODIFIERS:
            modifiers |= MODIFIERS[part]
        elif part != keys[0]:
            raise ValueError(f"unknown hotkey modifier: {part}")
    return Hotkey(KEYCODES[keys[0]], modifiers)


class PTTClient:
    """Send state changes and heartbeats so the server always fails closed."""

    def __init__(self, server_url: str):
        self.url = server_url.rstrip("/") + "/api/ptt"
        self.active = False
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._last_error = None
        self._thread = threading.Thread(target=self._run, name="ptt-heartbeat", daemon=True)

    def start(self):
        self._thread.start()

    def set_active(self, active: bool):
        if active != self.active:
            self.active = active
            self._wake.set()

    def close(self):
        self.active = False
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=1)
        self._post(False)

    def _post(self, active: bool):
        request = urllib.request.Request(
            self.url,
            data=json.dumps({"active": active}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=0.75):
                pass
            self._last_error = None
        except (OSError, urllib.error.HTTPError) as exc:
            error = str(exc)
            if error != self._last_error:
                print(f"Jarvis PTT: cannot update {self.url}: {error}", file=sys.stderr)
                self._last_error = error

    def _run(self):
        while not self._stop.is_set():
            self._wake.wait(0.5 if self.active else None)
            self._wake.clear()
            if not self._stop.is_set():
                self._post(self.active)


def run(key: Hotkey, server_url: str) -> int:
    if sys.platform != "darwin":
        print("Jarvis global hotkey is supported only on macOS.", file=sys.stderr)
        return 2

    import Quartz
    from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

    if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}):
        print(
            "Jarvis needs Accessibility permission. Enable your terminal or Python in "
            "System Settings > Privacy & Security > Accessibility, then restart this helper.",
            file=sys.stderr,
        )
        return 2

    client = PTTClient(server_url)
    client.start()
    pressed = False
    tap = None

    def callback(proxy, event_type, event, refcon):
        nonlocal pressed
        if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            pressed = False
            client.set_active(False)
            Quartz.CGEventTapEnable(tap, True)
            return event

        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        flags = Quartz.CGEventGetFlags(event)
        active = pressed
        if key.modifier_only and event_type == Quartz.kCGEventFlagsChanged and keycode == key.keycode:
            active = bool(
                Quartz.CGEventSourceKeyState(
                    Quartz.kCGEventSourceStateCombinedSessionState, key.keycode
                )
            )
        elif not key.modifier_only:
            if event_type == Quartz.kCGEventKeyDown and keycode == key.keycode:
                active = (flags & key.modifiers) == key.modifiers
            elif event_type == Quartz.kCGEventKeyUp and keycode == key.keycode:
                active = False
            elif event_type == Quartz.kCGEventFlagsChanged and pressed:
                active = (flags & key.modifiers) == key.modifiers
        if active != pressed:
            pressed = active
            client.set_active(active)
        return event

    mask = (
        Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
    )
    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly,
        mask,
        callback,
        None,
    )
    if tap is None:
        client.close()
        print(
            "macOS blocked the event tap. Grant Accessibility and Input Monitoring "
            "permission in System Settings > Privacy & Security, then restart the helper.",
            file=sys.stderr,
        )
        return 2

    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    run_loop = Quartz.CFRunLoopGetCurrent()
    Quartz.CFRunLoopAddSource(run_loop, source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)

    def stop(signum, frame):
        Quartz.CFRunLoopStop(run_loop)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print("Jarvis PTT ready. Hold the configured hotkey to talk; Ctrl-C to stop.")
    try:
        Quartz.CFRunLoopRun()
    finally:
        client.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default=CONFIG["hotkey"]["key"])
    parser.add_argument("--server-url", default=CONFIG["hotkey"]["server_url"])
    args = parser.parse_args()
    if not CONFIG["hotkey"]["enabled"]:
        print("Jarvis PTT is disabled. Set hotkey.enabled: true in server/config.yaml.")
        return 2
    try:
        key = parse_hotkey(args.key)
    except ValueError as exc:
        print(f"Invalid hotkey: {exc}", file=sys.stderr)
        return 2
    return run(key, args.server_url)


if __name__ == "__main__":
    raise SystemExit(main())
