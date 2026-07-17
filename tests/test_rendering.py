import unittest

from workspace_browser.rendering.dispatch import RenderKind, detect_render_kind


class RenderingTests(unittest.TestCase):
    def test_detect_text_and_markdown(self):
        self.assertEqual(RenderKind.TEXT, detect_render_kind("plain"))
        self.assertEqual(RenderKind.MARKDOWN, detect_render_kind("# heading"))

    def test_detect_table_and_download(self):
        self.assertEqual(RenderKind.TABLE, detect_render_kind([{"a": 1}]))
        self.assertEqual(RenderKind.DOWNLOAD, detect_render_kind({"download_path": "a"}))


if __name__ == "__main__":
    unittest.main()
