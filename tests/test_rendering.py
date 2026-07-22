import unittest

from matplotlib.figure import Figure
import numpy as np
import plotly.graph_objects as go

from sigvue.plugin import RasterizedHeatmap, aggregate_heatmap, rerasterize_heatmaps
from sigvue.rendering.dispatch import RenderKind, detect_render_kind
from sigvue.rendering.matplotlib_renderer import render_matplotlib_figure


class RenderingTests(unittest.TestCase):
    def test_heatmap_aggregation_preserves_exact_block_statistics(self):
        values = np.arange(24, dtype=float).reshape(4, 6)
        self.assertEqual(
            [[7.0, 9.0, 11.0], [19.0, 21.0, 23.0]],
            aggregate_heatmap(values, width=3, height=2, method="max").tolist(),
        )
        self.assertEqual(
            [[3.5, 5.5, 7.5], [15.5, 17.5, 19.5]],
            aggregate_heatmap(values, width=3, height=2, method="mean").tolist(),
        )
        self.assertEqual(
            [[3.5, 5.5, 7.5], [15.5, 17.5, 19.5]],
            aggregate_heatmap(values, width=3, height=2, method="median").tolist(),
        )

    def test_rasterized_heatmap_wraps_plotly_trace_and_retains_colorbar(self):
        raster = RasterizedHeatmap.create(
            x=np.arange(6),
            y=np.arange(4),
            z=np.arange(24, dtype=float).reshape(4, 6),
            zmin=0,
            zmax=23,
            colorscale="Viridis",
            colorbar={"title": "Power"},
            render_width=3,
            render_height=2,
            aggregation="max",
        )
        figure = go.Figure()
        self.assertEqual((2, 3), raster.render_shape)
        self.assertEqual(0, raster.add_to(figure))
        self.assertEqual(1, len(figure.layout.images))
        self.assertEqual((2, 2), np.asarray(figure.data[0].z).shape)
        self.assertEqual(0.0, figure.data[0].opacity)
        self.assertEqual("Power", figure.data[0].colorbar.title.text)

    def test_rasterized_heatmap_supports_plotly_default_coordinates(self):
        raster = RasterizedHeatmap.create(
            z=[[1.0, 2.0], [3.0, 4.0]],
            colorscale="Viridis",
            aggregation="median",
        )
        figure = go.Figure()
        raster.add_to(figure)
        image = figure.layout.images[0]
        self.assertEqual((-0.5, 1.5), (image.x, image.x + image.sizex))
        self.assertEqual((-0.5, 1.5), (image.y - image.sizey, image.y))

    def test_rasterized_heatmap_progressively_rerenders_visible_source_cells(self):
        figure = go.Figure()
        RasterizedHeatmap.create(
            x=np.arange(100),
            y=np.arange(80),
            z=np.arange(8_000, dtype=float).reshape(80, 100),
            colorscale="Viridis",
            render_width=10,
            render_height=8,
        ).add_to(figure)
        initial = figure.layout.images[0].source

        rerasterize_heatmaps(figure, {"xaxis": [40, 49], "yaxis": [20, 27]})

        image = figure.layout.images[0]
        self.assertNotEqual(initial, image.source)
        self.assertLess(image.sizex, 12)
        self.assertLess(image.sizey, 10)
        self.assertEqual((2, 2), np.asarray(figure.data[0].z).shape)

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
