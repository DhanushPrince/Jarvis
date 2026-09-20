#!/usr/bin/env python3
"""Jarvis system-wide, offline-first macOS dictation."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .audio import MicrophoneRecorder
from .hotkey import QuartzHotkeyListener, parse_hotkey
from .insertion import MacTextInjector
from .processing import make_cleanup, make_stt
from .session import Action, DictationSession
from .settings import load_settings


class StatusItem:
    LABELS = {"idle": "J", "recording": "J ●", "processing": "J …", "error": "J !"}

    def __init__(self, application):
        from AppKit import NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength

        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self.button = self.item.button()
        self.button.setTitle_(self.LABELS["idle"])
        menu = NSMenu.alloc().init()
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Jarvis Dictation", "terminate:", "q"
        )
        quit_item.setTarget_(application)
        menu.addItem_(quit_item)
        self.item.setMenu_(menu)

    def set(self, state: str) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self.button.setTitle_, self.LABELS[state])


class DictationController:
    def __init__(self, settings, status: StatusItem):
        self.settings = settings
        self.status = status
        self.session = DictationSession(settings.fail_closed_seconds)
        self.recorder = MicrophoneRecorder(settings.sample_rate)
        self.stt = make_stt(settings.stt_backend, settings.stt_model)
        self.cleanup = make_cleanup(
            settings.cleanup_enabled,
            settings.cleanup_backend,
            str(Path(settings.cleanup_model_path).expanduser()),
        )
        self.injector = MacTextInjector(settings.clipboard_restore_delay_ms)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dictation")
        self.watchdog = threading.Thread(target=self._watchdog, daemon=True)
        self.watchdog.start()

    def health(self) -> None:
        with self.lock:
            self.session.listener_healthy(time.monotonic())

    def down(self) -> None:
        with self.lock:
            action = self.session.hotkey_down(time.monotonic())
            if action is Action.START_CAPTURE:
                try:
                    self.recorder.start()
                    self.status.set("recording")
                except Exception as exc:
                    self.session.cancel()
                    self.status.set("error")
                    print(f"Could not start microphone: {exc}", file=sys.stderr)

    def up(self) -> None:
        with self.lock:
            action = self.session.hotkey_up()
            if action is not Action.PROCESS_CAPTURE:
                return
            samples = self.recorder.stop()
            self.status.set("processing")
        self.executor.submit(self._process, samples)

    def cancel(self) -> None:
        with self.lock:
            if self.session.cancel() is Action.CANCEL_CAPTURE:
                self.recorder.cancel()
                self.status.set("idle")

    def _process(self, samples) -> None:
        error = False
        try:
            if samples.size == 0:
                return
            text = self.stt.transcribe(samples, self.settings.sample_rate).strip()
            if not text:
                return
            try:
                text = self.cleanup.clean(text)
            except Exception as exc:
                print(f"Cleanup failed; inserting raw transcript: {exc}", file=sys.stderr)
            if not self.injector.insert(text):
                error = True
                print(
                    "Could not paste automatically. Transcript was left on the clipboard; "
                    "press Cmd+V.",
                    file=sys.stderr,
                )
        except Exception as exc:
            error = True
            print(f"Dictation failed: {exc}", file=sys.stderr)
        finally:
            with self.lock:
                self.session.processing_finished(error)
            self.status.set("error" if error else "idle")

    def _watchdog(self) -> None:
        while not self.stop_event.wait(0.1):
            with self.lock:
                if self.session.tick(time.monotonic()) is Action.CANCEL_CAPTURE:
                    self.recorder.cancel()
                    self.status.set("error")
                    print("Hotkey listener became unhealthy; microphone closed.", file=sys.stderr)

    def close(self) -> None:
        self.stop_event.set()
        self.cancel()
        self.watchdog.join(timeout=0.5)
        self.executor.shutdown(wait=False, cancel_futures=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="YAML file containing a dictation section")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("system-wide dictation runs only on macOS")
    try:
        settings = load_settings(args.config)
        hotkey = parse_hotkey(settings.hotkey)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))

    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

    application = NSApplication.sharedApplication()
    application.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    status = StatusItem(application)
    controller = DictationController(settings, status)
    listener = QuartzHotkeyListener(
        hotkey, controller.down, controller.up, controller.cancel, controller.health
    )
    try:
        listener.install()
    except (PermissionError, RuntimeError) as exc:
        controller.close()
        print(f"Cannot start dictation: {exc}", file=sys.stderr)
        return 2

    def stop(signum, frame):
        application.terminate_(None)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print(f"Jarvis Dictation ready. Hold {settings.hotkey}; press Escape to cancel.")
    try:
        application.run()
    finally:
        listener.close()
        controller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

