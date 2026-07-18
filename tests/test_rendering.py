import unittest

from matplotlib.figure import Figure

from sigvue.rendering.dispatch import RenderKind, detect_render_kind
from sigvue.rendering.matplotlib_renderer import render_matplotlib_figure


class RenderingTests(unittest.TestCase):
    def test_detect_text_and_markdown(self):
        self.assertEqual(RenderKind.TEXT, detect_render_kind("plain"))
        self.assertEqual(RenderKind.MARKDOWN, detect_render_kind("# heading"))
        self.assertEqual(RenderKind.MARKDOWN, detect_render_kind("Label: **value**"))
        self.assertEqual(RenderKind.TEXT, detect_render_kind("#hashtag"))

    def test_detect_table_and_download(self):
        self.assertEqual(RenderKind.TABLE, detect_render_kind([{"a": 1}]))
        self.assertEqual(RenderKind.DOWNLOAD, detect_render_kind({"download_path": "a"}))

    def test_detect_and_encode_matplotlib_figure(self):
        figure = Figure()
        figure.subplots().plot([0, 1], [1, 0])
        self.assertEqual(RenderKind.MATPLOTLIB, detect_render_kind(figure))
        self.assertTrue(render_matplotlib_figure(figure).startswith("iVBORw0KGgo"))
        self.assertEqual(1, len(figure.axes))


if __name__ == "__main__":
    unittest.main()
