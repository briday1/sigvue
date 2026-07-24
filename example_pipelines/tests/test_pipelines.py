import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import plotly.graph_objects as go

from example_pipelines.comms.workspace import create_workspace as create_comms_workspace
from example_pipelines.plugins import CallableAnalysis, CallablePresentation
from example_pipelines.plugins.sigmf import (
    SIGMF_DISCOVERY_COLUMNS,
    SigMFAnnotator,
    SigMFExporter,
    SigMFRecording,
    WaterfallSigMFAnnotator,
    WindowedSigMFDelivery,
    append_annotation,
)
from example_pipelines.scripts.generate_comms import generate as generate_comms
from example_pipelines.scripts.generate_lte import generate as generate_lte
from example_pipelines.style import ORANGE, TEAL, heatmap_grid_color
from example_pipelines.waterfall.workspace import create_workspace as create_waterfall_workspace
from sigvue.web.application import SigvueApp


class ExamplePipelineTests(unittest.TestCase):
    def test_synthetic_lte_generator_and_waterfall_workspace(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "lte"
            generated = generate_lte(root)
            self.assertEqual(2, len(generated))
            self.assertTrue(all(metadata.is_file() and data.is_file() for metadata, data in generated))
            for metadata_path, _ in generated:
                captured_at = json.loads(
                    metadata_path.read_text()
                )["captures"][0]["core:datetime"]
                self.assertTrue(captured_at.endswith("Z"))
                self.assertNotIn("+00:00", captured_at)

            workspace = create_waterfall_workspace({"data_root": root})
            self.assertIsInstance(workspace.delivery, WindowedSigMFDelivery)
            self.assertIsInstance(workspace.analysis, CallableAnalysis)
            self.assertIsInstance(workspace.presentation, CallablePresentation)
            self.assertIsInstance(workspace.annotator, WaterfallSigMFAnnotator)
            self.assertIsInstance(workspace.exporter, SigMFExporter)
            self.assertEqual(SIGMF_DISCOVERY_COLUMNS, workspace.discovery_columns)
            items = workspace.discover_items()
            self.assertEqual(2, len(items))
            self.assertEqual({()}, {item.navigation_path for item in items})
            self.assertEqual(
                {"Synthetic LTE-like downlink", "Synthetic LTE-like uplink"},
                {item.title for item in items},
            )
            resource = workspace.source.discover()[0]
            recording = workspace.source.open(resource)
            self.assertIsInstance(recording, SigMFRecording)
            self.assertEqual((1, 8), recording.read(0, 8).shape)

            opened = workspace.open_item(items[0].identifier)
            self.assertIsNotNone(opened.page.annotation)
            self.assertEqual((), opened.page.annotation.discover_callback())
            self.assertEqual("windowed", opened.page.playback.mode)
            self.assertEqual("Mean received power (dBFS)", opened.page.playback.overview_label)
            self.assertRegex(str(opened.page.statistics["Buffer memory"]), r"^[0-9.]+ [KMGT]?i?B$")
            self.assertEqual(1, len(opened.page.views))
            controls = {control.name: control for control in opened.page.controls}
            self.assertEqual("select", controls["fft_size"].control_type)
            self.assertEqual("select", controls["overlap_percent"].control_type)
            self.assertEqual("colormap", controls["colormap"].control_type)
            self.assertEqual("limits", controls["dbfs_limits"].control_type)
            self.assertEqual("limits", controls["spectrum_dbfs_limits"].control_type)
            self.assertEqual("spectrum_style", controls["spectrum_style_color"].picker)
            self.assertEqual("toggle", controls["show_colorbar"].control_type)
            self.assertEqual("Annotations", controls["show_annotations"].group)
            self.assertEqual(
                "Annotations",
                controls["annotation_region_color"].group,
            )
            self.assertEqual(
                "Annotations",
                controls["annotation_region_width"].group,
            )
            self.assertEqual(
                "Annotations",
                controls["annotation_region_opacity"].group,
            )
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

            append_annotation(
                Path(items[0].source_reference),
                {
                    "core:sample_start": 2_000,
                    "core:sample_count": 4_000,
                    "core:comment": "Existing review region",
                    "core:freq_lower_edge": recording.center_frequency - 250_000,
                    "core:freq_upper_edge": recording.center_frequency + 250_000,
                },
            )
            axis_lower = float(figure.layout.xaxis2.range[0])
            first_center = float(figure.data[1].x[0])
            append_annotation(
                Path(items[0].source_reference),
                {
                    "core:sample_start": 2_000,
                    "core:sample_count": 4_000,
                    "core:comment": "Outer half-bin review region",
                    "core:freq_lower_edge": (
                        axis_lower + 0.2 * (first_center - axis_lower)
                    ) * 1e6,
                    "core:freq_upper_edge": (
                        axis_lower + 0.8 * (first_center - axis_lower)
                    ) * 1e6,
                },
            )
            annotated = workspace.open_item(items[0].identifier)
            self.assertEqual(
                2,
                len(annotated.page.annotation.discover_callback()),
            )
            annotated_figure = annotated.page.views[0].callback({
                "annotation_region_color": "#ff8800",
                "annotation_region_width": "3.5",
                "annotation_region_opacity": "0.35",
            })
            annotation_trace = next(
                trace
                for trace in annotated_figure.data
                if trace.name == "Annotations"
            )
            self.assertEqual("#ff8800", annotation_trace.line.color)
            self.assertEqual(3.5, annotation_trace.line.width)
            self.assertEqual(0.35, annotation_trace.opacity)
            annotation_details = next(
                trace
                for trace in annotated_figure.data
                if trace.name == "Annotation details"
            )
            self.assertTrue(
                any(
                    "Existing review region" in text
                    for text in annotation_details.text
                )
            )
            self.assertTrue(
                any(
                    "Outer half-bin review region" in text
                    for text in annotation_details.text
                )
            )

    def test_synthetic_comms_generator_and_windowed_workspace(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "comms"
            generated = generate_comms(root)
            self.assertEqual(3, len(generated))
            self.assertTrue(all(metadata.is_file() and data.is_file() for metadata, data in generated))
            for metadata_path, _ in generated:
                captured_at = json.loads(
                    metadata_path.read_text()
                )["captures"][0]["core:datetime"]
                self.assertTrue(captured_at.endswith("Z"))
                self.assertNotIn("+00:00", captured_at)

            workspace = create_comms_workspace({"data_root": root})
            self.assertIsInstance(workspace.delivery, WindowedSigMFDelivery)
            self.assertIsInstance(workspace.analysis, CallableAnalysis)
            self.assertIsInstance(workspace.presentation, CallablePresentation)
            self.assertIsInstance(workspace.annotator, SigMFAnnotator)
            self.assertIsInstance(workspace.exporter, SigMFExporter)
            self.assertEqual(SIGMF_DISCOVERY_COLUMNS, workspace.discovery_columns)
            items = workspace.discover_items()
            self.assertEqual(3, len(items))
            self.assertEqual(
                {"Synthetic QPSK", "Synthetic 16-QAM", "Synthetic 64-QAM"},
                {item.title for item in items},
            )

            for item in items:
                opened = workspace.open_item(item.identifier)
                self.assertIsNotNone(opened.page.annotation)
                self.assertEqual((), opened.page.annotation.discover_callback())
                self.assertEqual("windowed", opened.page.playback.mode)
                self.assertEqual("Mean received power (dBFS)", opened.page.playback.overview_label)
                self.assertRegex(str(opened.page.statistics["Buffer memory"]), r"^[0-9.]+ [KMGT]?i?B$")
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

    def test_lazy_comms_workspace_only_builds_the_selected_tab(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "comms"
            generate_comms(root)
            workspace = create_comms_workspace({"data_root": root})
            item_id = workspace.discover_items()[0].identifier
            app = SigvueApp()
            app.register_workspace(workspace)

            with (
                patch(
                    "example_pipelines.comms.presentation.constellation_figure",
                    return_value=go.Figure(),
                ) as constellation,
                patch(
                    "example_pipelines.comms.presentation.eye_figure",
                    return_value=go.Figure(),
                ) as eye,
            ):
                initial = app.open_item(workspace.metadata.identifier, item_id)
                switched = app.open_item(
                    workspace.metadata.identifier,
                    item_id,
                    {"__view_selection___tabs": "1"},
                )

            self.assertTrue(workspace.lazy_views)
            self.assertEqual(
                ["constellation"],
                [view["name"] for view in initial["page"]["rendered_views"]],
            )
            self.assertEqual(
                ["eye"],
                [view["name"] for view in switched["page"]["rendered_views"]],
            )
            constellation.assert_called_once()
            eye.assert_called_once()
