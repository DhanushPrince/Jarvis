"""Pasteboard-first text insertion with lossless failure behavior."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class ClipboardSnapshot:
    items: tuple[tuple[tuple[str, bytes], ...], ...]


class Pasteboard(Protocol):
    def snapshot(self) -> ClipboardSnapshot: ...
    def write_text(self, text: str) -> int: ...
    def restore(self, snapshot: ClipboardSnapshot) -> None: ...
    def change_count(self) -> int: ...


class ClipboardInserter:
    def __init__(
        self,
        pasteboard: Pasteboard,
        paste: Callable[[], None],
        restore_delay: float = 0.15,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.pasteboard = pasteboard
        self.paste = paste
        self.restore_delay = restore_delay
        self.sleep = sleep

    def copy_only(self, text: str) -> bool:
        self.pasteboard.write_text(text)
        return False

    def insert(self, text: str) -> bool:
        """Paste and restore; on any failure, leave `text` on the clipboard."""
        snapshot = self.pasteboard.snapshot()
        inserted_change = self.pasteboard.write_text(text)
        try:
            self.paste()
            self.sleep(self.restore_delay)
            # Do not overwrite something the user copied during the restore delay.
            if self.pasteboard.change_count() == inserted_change:
                self.pasteboard.restore(snapshot)
            return True
        except Exception:
            self.pasteboard.write_text(text)
            return False


class MacPasteboard:
    def __init__(self):
        from AppKit import NSPasteboard

        self._pasteboard = NSPasteboard.generalPasteboard()

    def snapshot(self) -> ClipboardSnapshot:
        items = []
        for item in self._pasteboard.pasteboardItems() or []:
            values = []
            for type_name in item.types() or []:
                data = item.dataForType_(type_name)
                if data is not None:
                    values.append((str(type_name), bytes(data)))
            items.append(tuple(values))
        return ClipboardSnapshot(tuple(items))

    def write_text(self, text: str) -> int:
        from AppKit import NSPasteboardTypeString

        self._pasteboard.clearContents()
        if not self._pasteboard.setString_forType_(text, NSPasteboardTypeString):
            raise RuntimeError("could not write transcript to the pasteboard")
        return self.change_count()

    def restore(self, snapshot: ClipboardSnapshot) -> None:
        from AppKit import NSPasteboardItem
        from Foundation import NSData

        restored = []
        for values in snapshot.items:
            item = NSPasteboardItem.alloc().init()
            for type_name, value in values:
                item.setData_forType_(NSData.dataWithBytes_length_(value, len(value)), type_name)
            restored.append(item)
        self._pasteboard.clearContents()
        if restored and not self._pasteboard.writeObjects_(restored):
            raise RuntimeError("could not restore the pasteboard")

    def change_count(self) -> int:
        return int(self._pasteboard.changeCount())


class MacTextInjector:
    def __init__(self, restore_delay_ms: int = 150):
        self._inserter = ClipboardInserter(
            MacPasteboard(),
            self._paste,
            restore_delay=restore_delay_ms / 1000,
        )

    @staticmethod
    def _paste() -> None:
        import Quartz

        down = Quartz.CGEventCreateKeyboardEvent(None, 9, True)  # ANSI V
        up = Quartz.CGEventCreateKeyboardEvent(None, 9, False)
        if down is None or up is None:
            raise RuntimeError("could not create Cmd+V keyboard events")
        Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

    def insert(self, text: str) -> bool:
        from ApplicationServices import AXIsProcessTrusted

        if not AXIsProcessTrusted():
            return self._inserter.copy_only(text)
        return self._inserter.insert(text)

