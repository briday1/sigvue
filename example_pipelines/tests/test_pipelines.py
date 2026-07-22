from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from example_pipelines.comms.workspace import create_workspace as create_comms_workspace
from example_pipelines.scripts.generate_comms import generate as generate_comms
from example_pipelines.scripts.generate_lte import generate as generate_lte
from example_pipelines.style import ORANGE, TEAL, heatmap_grid_color
from example_pipelines.waterfall.workspace import create_workspace as create_waterfall_workspace


class ExamplePipelineTests(unittest.TestCase):
    def test_synthetic_lte_generator_and_waterfall_workspace(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "lte"
            generated = generate_lte(root)
            self.assertEqual(2, len(generated))
            self.assertTrue(all(metadata.is_file() and data.is_file() for metadata, data in generated))

            workspace = create_waterfall_workspace({"data_root": root})
            items = workspace.discover_items()
            self.assertEqual(2, len(items))
            self.assertEqual({("lte",)}, {item.navigation_path for item in items})
            self.assertEqual(
                {"Synthetic LTE-like downlink", "Synthetic LTE-like uplink"},
                {item.title for item in items},
            )

            opened = workspace.open_item(items[0].identifier)
            self.assertEqual("windowed", opened.page.playback.mode)
            self.assertEqual("Mean received power (dBFS)", opened.page.playback.overview_label)
            self.assertEqual(1, len(opened.page.views))
            controls = {control.name: control for control in opened.page.controls}
            self.assertEqual("select", controls["fft_size"].control_type)
            self.assertEqual("select", controls["overlap_percent"].control_type)
            self.assertEqual("colormap", controls["colormap"].control_type)
            self.assertEqual("limits", controls["dbfs_limits"].control_type)
            self.assertEqual("limits", controls["spectrum_dbfs_limits"].control_type)
            self.assertEqual("spectrum_style", controls["spectrum_style_color"].picker)
            self.assertEqual("toggle", controls["show_colorbar"].control_type)
            self.assertEqual("Raster rendering", controls["render_width"].group)
            figure = opened.page.views[0].callback({})
            self.assertEqual(["scatter", "heatmap"], [trace.type for trace in figure.data])
            self.assertEqual((44, 1024), np.asarray(figure.data[1].z).shape)
            self.assertEqual(0, len(figure.layout.images))
            self.assertEqual(heatmap_grid_color("light"), figure.layout.xaxis2.gridcolor)
            self.assertEqual(0.35, figure.layout.xaxis2.gridwidth)
            limited = opened.page.views[0].callback({"dbfs_limits": "-80,-30"})
            self.assertEqual((-80.0, -30.0), (limited.data[1].zmin, limited.data[1].zmax))
            spectrum_limited = opened.page.views[0].callback({"spectrum_dbfs_limits": "-70,-25"})
            self.assertEqual((-70.0, -25.0), tuple(spectrum_limited.layout.yaxis.range))

    def test_synthetic_comms_generator_and_windowed_workspace(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "comms"
            generated = generate_comms(root)
            self.assertEqual(3, len(generated))
            self.assertTrue(all(metadata.is_file() and data.is_file() for metadata, data in generated))

            workspace = create_comms_workspace({"data_root": root})
            items = workspace.discover_items()
            self.assertEqual(3, len(items))
            self.assertEqual(
                {"Synthetic QPSK", "Synthetic 16-QAM", "Synthetic 64-QAM"},
                {item.title for item in items},
            )

            for item in items:
                opened = workspace.open_item(item.identifier)
                self.assertEqual("windowed", opened.page.playback.mode)
                self.assertEqual("Mean received power (dBFS)", opened.page.playback.overview_label)
                self.assertGreater(len(opened.page.playback.overview_values), 1)
                self.assertLess(
                    opened.page.playback.window_end_seconds - opened.page.playback.window_start_seconds,
                    opened.page.playback.duration_seconds,
                )
                self.assertEqual(["constellation", "eye"], [view.name for view in opened.page.views])
                figures = [view.callback({}) for view in opened.page.views]
                self.assertEqual("scattergl", figures[0].data[0].type)
                self.assertEqual(["scattergl", "scattergl"], [trace.type for trace in figures[1].data])
                dark_figures = [view.callback({"__theme": "dark"}) for view in opened.page.views]
                self.assertEqual("#10252d", dark_figures[0].layout.paper_bgcolor)
                self.assertEqual(TEAL, dark_figures[0].data[0].marker.color)
                self.assertEqual([TEAL, ORANGE], [trace.line.color for trace in dark_figures[1].data])
