import sys
import unittest
from types import SimpleNamespace

sys.modules.setdefault(
    "config",
    SimpleNamespace(
        CONFIG={"hotkey": {"enabled": False, "key": "right_command", "server_url": ""}}
    ),
)

from hotkey_helper import parse_hotkey


class ParseHotkeyTest(unittest.TestCase):
    def test_modifier_key(self):
        self.assertEqual(parse_hotkey("right_command").keycode, 54)

    def test_chord_and_aliases(self):
        hotkey = parse_hotkey("cmd+shift+space")
        self.assertEqual(hotkey.keycode, 49)
        self.assertNotEqual(hotkey.modifiers, 0)

    def test_rejects_modifier_without_key(self):
        with self.assertRaises(ValueError):
            parse_hotkey("command+shift")


if __name__ == "__main__":
    unittest.main()
