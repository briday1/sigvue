from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from sigvue.web import desktop


class FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeWindow:
    def __init__(self) -> None:
        self.events = SimpleNamespace(
            loaded=FakeEvent(),
            restored=FakeEvent(),
        )
        self.scripts = []
        self.fullscreen_toggles = 0
        self.selected_directory = "/tmp/scientific-data"

    def evaluate_js(self, script):
        self.scripts.append(script)

    def toggle_fullscreen(self):
        self.fullscreen_toggles += 1

    def create_file_dialog(self, dialog_type):
        self.dialog_type = dialog_type
        return (self.selected_directory,)


class DesktopTests(unittest.TestCase):
    def test_shared_desktop_hosts_any_profile_and_native_controls(self):
        with TemporaryDirectory() as directory:
            profile = Path(directory) / "browser.toml"
            profile.write_text(
                '[browser]\ntitle = "Desktop Fixture"\n',
                encoding="utf-8",
            )
            result = {}
            window = FakeWindow()

            def create_window(title, url, **options):
                result.update(title=title, url=url, options=options)
                return window

            def start(**options):
                with urlopen(f"{result['url']}/health", timeout=5) as response:
                    result["health"] = json.load(response)
                for handler in window.events.loaded.handlers:
                    handler(window)
                result["start_options"] = options

            fake_webview = SimpleNamespace(
                FileDialog=SimpleNamespace(FOLDER=20),
                create_window=create_window,
                start=start,
            )
            with (
                patch.dict(sys.modules, {"webview": fake_webview}),
                patch.object(
                    sys,
                    "argv",
                    [
                        "sigvue-desktop",
                        "--config",
                        str(profile),
                        "--width",
                        "1200",
                        "--height",
                        "700",
                    ],
                ),
            ):
                desktop.main()

        self.assertEqual("Desktop Fixture", result["title"])
        self.assertTrue(result["url"].startswith("http://127.0.0.1:"))
        self.assertEqual({"status": "ok"}, result["health"])
        self.assertEqual(1200, result["options"]["width"])
        self.assertEqual(700, result["options"]["height"])
        self.assertEqual((900, 600), result["options"]["min_size"])
        bridge = result["options"]["js_api"]
        self.assertEqual("/tmp/scientific-data", bridge.choose_directory())
        self.assertEqual(20, window.dialog_type)
        self.assertTrue(bridge.toggle_fullscreen())
        for handler in window.events.restored.handlers:
            handler()
        self.assertFalse(bridge.fullscreen_state())
        self.assertEqual(1, window.fullscreen_toggles)
        self.assertEqual(2, len(window.scripts))
        self.assertIn("#fullscreen-toggle", window.scripts[0])
        self.assertIn("stopImmediatePropagation", window.scripts[0])
        self.assertIn("event.key !== 'Escape'", window.scripts[0])
        self.assertEqual(
            "window.__sigvueSetNativeFullscreen?.(false)",
            window.scripts[1],
        )
        self.assertEqual({"debug": False}, result["start_options"])


if __name__ == "__main__":
    unittest.main()
