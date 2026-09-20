import tempfile
import unittest
from pathlib import Path

from dictation.hotkey import Hotkey, parse_hotkey
from dictation.insertion import ClipboardInserter, ClipboardSnapshot
from dictation.session import Action, DictationSession, State
from dictation.settings import load_settings


class SessionTests(unittest.TestCase):
    def test_hold_release_and_finish(self):
        session = DictationSession()
        self.assertIs(session.hotkey_down(10), Action.START_CAPTURE)
        self.assertIs(session.state, State.RECORDING)
        self.assertIs(session.hotkey_up(), Action.PROCESS_CAPTURE)
        session.processing_finished()
        self.assertIs(session.state, State.IDLE)

    def test_escape_and_listener_timeout_fail_closed(self):
        session = DictationSession(1.5)
        session.hotkey_down(10)
        self.assertIs(session.tick(11.49), Action.NONE)
        self.assertIs(session.tick(11.5), Action.CANCEL_CAPTURE)
        self.assertIs(session.state, State.IDLE)
        session.hotkey_down(20)
        self.assertIs(session.cancel(), Action.CANCEL_CAPTURE)

    def test_listener_health_refreshes_watchdog(self):
        session = DictationSession(1.5)
        session.hotkey_down(10)
        session.listener_healthy(11)
        self.assertIs(session.tick(12.49), Action.NONE)
        self.assertIs(session.tick(12.5), Action.CANCEL_CAPTURE)

    def test_processing_ignores_press_and_error_recovers_on_next_press(self):
        session = DictationSession()
        session.hotkey_down(1)
        session.hotkey_up()
        self.assertIs(session.hotkey_down(2), Action.NONE)
        session.processing_finished(error=True)
        self.assertIs(session.hotkey_down(3), Action.START_CAPTURE)


class HotkeyTests(unittest.TestCase):
    def test_modifier_and_chord_parsing(self):
        self.assertEqual(parse_hotkey("right_command"), Hotkey(54, modifier_only=True))
        chord = parse_hotkey("ctrl+space")
        self.assertEqual(chord.keycode, 49)
        self.assertNotEqual(chord.modifiers, 0)

    def test_invalid_hotkey(self):
        with self.assertRaises(ValueError):
            parse_hotkey("command+not-a-key")


class SettingsTests(unittest.TestCase):
    def test_yaml_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                "dictation:\n"
                "  hotkey: left_command\n"
                "  cleanup_enabled: false\n"
                "  fail_closed_seconds: 1.0\n"
            )
            settings = load_settings(path, {"DICTATION_HOTKEY": "control+space"})
        self.assertEqual(settings.hotkey, "control+space")
        self.assertEqual(settings.fail_closed_seconds, 1.0)
        self.assertFalse(settings.cleanup_enabled)

    def test_rejects_unsafe_timeout_and_missing_cleanup_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("dictation:\n  fail_closed_seconds: 1.6\n")
            with self.assertRaises(ValueError):
                load_settings(path, {})
            path.write_text("dictation:\n  cleanup_enabled: true\n")
            with self.assertRaises(ValueError):
                load_settings(path, {})


class FakePasteboard:
    def __init__(self, text="old"):
        self.text = text
        self.count = 1
        self.fail_restore = False

    def snapshot(self):
        return ClipboardSnapshot(((("public.utf8-plain-text", self.text.encode()),),))

    def write_text(self, text):
        self.text = text
        self.count += 1
        return self.count

    def restore(self, snapshot):
        if self.fail_restore:
            raise RuntimeError("restore failed")
        self.text = snapshot.items[0][0][1].decode()
        self.count += 1

    def change_count(self):
        return self.count


class ClipboardTests(unittest.TestCase):
    def test_success_restores_clipboard(self):
        pasteboard = FakePasteboard()
        pasted = []
        inserter = ClipboardInserter(pasteboard, lambda: pasted.append(pasteboard.text), sleep=lambda _: None)
        self.assertTrue(inserter.insert("dictated"))
        self.assertEqual(pasted, ["dictated"])
        self.assertEqual(pasteboard.text, "old")

    def test_paste_or_restore_failure_leaves_transcript(self):
        for fail_paste in (True, False):
            with self.subTest(fail_paste=fail_paste):
                pasteboard = FakePasteboard()
                pasteboard.fail_restore = not fail_paste

                def paste():
                    if fail_paste:
                        raise RuntimeError("paste failed")

                self.assertFalse(
                    ClipboardInserter(pasteboard, paste, sleep=lambda _: None).insert("dictated")
                )
                self.assertEqual(pasteboard.text, "dictated")

    def test_does_not_overwrite_new_user_copy(self):
        pasteboard = FakePasteboard()

        def user_copies(_):
            pasteboard.write_text("new user copy")

        inserter = ClipboardInserter(pasteboard, lambda: None, sleep=user_copies)
        self.assertTrue(inserter.insert("dictated"))
        self.assertEqual(pasteboard.text, "new user copy")


if __name__ == "__main__":
    unittest.main()

