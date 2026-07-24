import json
import unittest

from matplotlib.figure import Figure
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from sigvue.plugin import add_viewport_heatmap, aggregate_heatmap
from sigvue.rendering.dispatch import RenderKind, detect_render_kind
from sigvue.rendering.matplotlib_renderer import render_matplotlib_figure


class RenderingTests(unittest.TestCase):
    def test_render_kind_preserves_string_enum_behavior(self):
        self.assertIsInstance(RenderKind.PLOTLY, str)
        self.assertEqual("plotly", RenderKind.PLOTLY)
        self.assertEqual("plotly", str(RenderKind.PLOTLY))
        self.assertIs(RenderKind.PLOTLY, RenderKind("plotly"))
        self.assertEqual('{"kind": "plotly"}', json.dumps({"kind": RenderKind.PLOTLY}))

    def test_heatmap_aggregation_preserves_exact_block_statistics(self):
        values = np.arange(24, dtype=float).reshape(4, 6)
        self.assertEqual([[7, 9, 11], [19, 21, 23]], aggregate_heatmap(values, width=3, height=2, method="max").tolist())
        self.assertEqual([[3.5, 5.5, 7.5], [15.5, 17.5, 19.5]], aggregate_heatmap(values, width=3, height=2).tolist())

    def test_initial_view_rasters_full_source_to_budget(self):
        figure = go.Figure()
        add_viewport_heatmap(
            figure, x=np.arange(100), y=np.arange(80), z=np.arange(8_000).reshape(80, 100),
            colorscale="Viridis", render_width=10, render_height=8,
        )
        self.assertEqual(1, len(figure.layout.images))
        self.assertEqual((2, 2), np.asarray(figure.data[0].z).shape)
        self.assertTrue(figure._sigvue_viewport_heatmap)

    def test_zoom_rasters_only_requested_source_region(self):
        figure = go.Figure()
        add_viewport_heatmap(
            figure, viewport={"xaxis": {"range": [40, 49], "base": [-0.5, 99.5]}, "yaxis": {"range": [20, 27], "base": [-0.5, 79.5]}},
            x=np.arange(100), y=np.arange(80), z=np.arange(8_000).reshape(80, 100),
            colorscale="Viridis", render_width=5, render_height=4,
        )
        image = figure.layout.images[0]
        self.assertLess(image.sizex, 12)
        self.assertLess(image.sizey, 10)

    def test_visible_data_smaller_than_budget_is_native_and_unmodified(self):
        source = np.arange(8_000).reshape(80, 100)
        figure = go.Figure()
        add_viewport_heatmap(
            figure, viewport={"xaxis": {"range": [40, 44], "base": [-0.5, 99.5]}, "yaxis": {"range": [20, 23], "base": [-0.5, 79.5]}},
            x=np.arange(100), y=np.arange(80), z=source,
            colorscale="Viridis", render_width=10, render_height=8,
        )
        self.assertEqual(0, len(figure.layout.images))
        np.testing.assert_array_equal(np.asarray(figure.data[0].z), source[20:24, 40:45])
        np.testing.assert_array_equal(np.asarray(figure.data[0].x), np.arange(40, 45))
        np.testing.assert_array_equal(np.asarray(figure.data[0].y), np.arange(20, 24))

    def test_native_visible_data_preserves_coordinate_edges(self):
        source = np.arange(8_000).reshape(80, 100)
        figure = go.Figure()
        add_viewport_heatmap(
            figure,
            viewport={"xaxis": {"range": [40, 44], "base": [-0.5, 99.5]}, "yaxis": {"range": [20, 23], "base": [0, 80]}},
            x=np.arange(100), y=np.arange(81), z=source,
            colorscale="Viridis", render_width=10, render_height=8,
        )
        self.assertEqual(0, len(figure.layout.images))
        np.testing.assert_array_equal(np.asarray(figure.data[0].z), source[20:23, 40:45])
        np.testing.assert_array_equal(np.asarray(figure.data[0].y), np.arange(20, 24))

    def test_each_zoom_is_evaluated_against_original_source(self):
        source = np.arange(600_000).reshape(600, 1_000)
        first = go.Figure()
        add_viewport_heatmap(
            first, viewport={"xaxis": {"range": [200, 699], "base": [-0.5, 999.5]}},
            x=np.arange(1_000), y=np.arange(600), z=source, colorscale="Viridis",
            render_width=100, render_height=60,
        )
        second = go.Figure()
        add_viewport_heatmap(
            second, viewport={"xaxis": {"range": [350, 449], "base": [-0.5, 999.5]}},
            x=np.arange(1_000), y=np.arange(600), z=source, colorscale="Viridis",
            render_width=100, render_height=60,
        )
        self.assertGreater(first.layout.images[0].sizex, second.layout.images[0].sizex)
        self.assertLessEqual(second.layout.images[0].x, 350)
        self.assertGreaterEqual(second.layout.images[0].x + second.layout.images[0].sizex, 449)

    def test_buffer_move_preserves_zoom_span(self):
        figure = go.Figure()
        add_viewport_heatmap(
            figure, viewport={"yaxis": {"range": [20, 50], "base": [-0.5, 99.5]}},
            x=np.arange(100), y=np.arange(200, 400), z=np.arange(20_000).reshape(200, 100),
            colorscale="Viridis", render_width=20, render_height=20,
        )
        self.assertGreaterEqual(figure.layout.images[0].sizey, 30)
        self.assertLessEqual(figure.layout.images[0].sizey, 32)

    def test_x_only_zoom_resolves_matched_subplot_axis(self):
        figure = make_subplots(rows=2, cols=1, shared_xaxes=True)
        figure.add_trace(go.Scatter(x=np.arange(100), y=np.arange(100)), row=1, col=1)
        add_viewport_heatmap(
            figure, viewport={"xaxis": [40, 49]}, x=np.arange(100), y=np.arange(80),
            z=np.arange(8_000).reshape(80, 100), colorscale="Viridis",
            render_width=5, render_height=8, row=2, col=1,
        )
        self.assertLess(figure.layout.images[0].sizex, 12)

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


if __name__ == "__main__":
    unittest.main()
