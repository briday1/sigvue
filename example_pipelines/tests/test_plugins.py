import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import plotly.graph_objects as go

from example_pipelines.plugins import (
    CallableAnalysis,
    CallableDelivery,
    CallablePresentation,
    add_time_frequency_annotation_regions,
)
from example_pipelines.plugins.sigmf import (
    SigMFAnnotator,
    SigMFExporter,
    SigMFWindow,
    add_sigmf_annotation,
    load_sigmf_recording,
    power_overview,
    read_sigmf_annotations,
    sigmf_source,
    write_array_bundle_export,
    write_sample_export,
    write_sigmf_recording,
)
from sigvue.plugin import Annotation, AnnotationRequest, ExportRequest


class PluginHelperTests(unittest.TestCase):
    @staticmethod
    def samples() -> np.ndarray:
        return np.asarray(
            [
                [
                    0.0 + 0.0j,
                    0.25 + 0.1j,
                    -0.4 + 0.2j,
                    0.7 - 0.5j,
                ],
                [
                    0.1 - 0.2j,
                    -0.3 + 0.4j,
                    0.5 + 0.6j,
                    -0.7 - 0.1j,
                ],
            ],
            dtype=np.complex64,
        )

    def test_callable_adapters_keep_framework_lifecycle_semantics(self):
        delivery = CallableDelivery(lambda value, ui: value + 1)
        analysis = CallableAnalysis(
            lambda value, settings: value * settings,
            lambda value, ui: 3,
        )
        presented = []
        presentation = CallablePresentation(
            lambda products, ui: presented.append(products)
        )

        self.assertEqual(3, delivery.prepare(2, object()))
        self.assertTrue(analysis.has_configuration)
        self.assertEqual(3, analysis.configure(2, object()))
        self.assertEqual(6, analysis.process(2, 3))
        presentation.present(6, object())
        self.assertEqual([6], presented)
        self.assertFalse(
            CallableAnalysis(lambda value, settings: value).has_configuration
        )
        with self.assertRaisesRegex(TypeError, "callable"):
            CallablePresentation(None)

    def test_generator_module_entry_point_imports_the_local_plugins(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "example_pipelines.scripts.generate_all",
                "--help",
            ],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("Generate every dataset", completed.stdout)

    def test_plot_regions_preserve_geometry_and_clip_hover_coordinates(self):
        figure = go.Figure()
        add_time_frequency_annotation_regions(
            figure,
            (
                Annotation(
                    "region",
                    0.002,
                    0.003,
                    comment="<review>",
                    frequency_lower_hz=806_000_000,
                    frequency_upper_hz=807_000_000,
                ),
            ),
            time_range=(3.0, 4.0),
            frequency_range=(806.25, 806.75),
            seconds_to_axis=1e3,
            hertz_to_axis=1e-6,
            time_unit="ms",
            frequency_unit="MHz",
        )

        self.assertEqual(["scatter", "scatter"], [
            trace.type for trace in figure.data
        ])
        self.assertEqual(
            (806.0, 807.0, 807.0, 806.0, 806.0, None),
            tuple(figure.data[0].x),
        )
        self.assertEqual(
            (2.0, 2.0, 5.0, 5.0, 2.0, None),
            tuple(figure.data[0].y),
        )
        self.assertEqual((806.5,), tuple(figure.data[1].x))
        self.assertEqual((3.5,), tuple(figure.data[1].y))
        self.assertIn("&lt;review&gt;", figure.data[1].text[0])
        with self.assertRaisesRegex(ValueError, "finite and increasing"):
            add_time_frequency_annotation_regions(
                go.Figure(),
                (),
                time_range=(1.0, 1.0),
                frequency_range=(0.0, 1.0),
            )

    def test_writer_and_reader_cover_supported_and_legacy_datatypes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for datatype, tolerance in (
                ("cf32_le", 1e-7),
                ("ci16_le", 4e-5),
            ):
                metadata_path, _ = write_sigmf_recording(
                    root,
                    datatype,
                    self.samples(),
                    2_000.0,
                    datatype=datatype,
                )
                recording = load_sigmf_recording(metadata_path)
                np.testing.assert_allclose(
                    self.samples()[:, 1:3],
                    recording.read(1, 2),
                    atol=tolerance,
                )
                self.assertEqual((2, 4), recording.read(0, 4).shape)

            legacy_metadata, _ = write_sigmf_recording(
                root,
                "legacy",
                self.samples(),
                2_000.0,
                datatype="ci16_le",
            )
            payload = json.loads(legacy_metadata.read_text())
            payload["global"]["core:datatype"] = "sc16_le"
            legacy_metadata.write_text(json.dumps(payload))
            np.testing.assert_allclose(
                self.samples(),
                load_sigmf_recording(legacy_metadata).read(0, 4),
                atol=4e-5,
            )
            with self.assertRaisesRegex(ValueError, "legacy reads only"):
                write_sigmf_recording(
                    root,
                    "invalid-sc16",
                    self.samples(),
                    2_000.0,
                    datatype="sc16_le",
                )

    def test_writer_rejects_unreadable_or_escaping_recordings(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "at least one channel"):
                write_sigmf_recording(
                    root,
                    "empty",
                    np.empty((0, 4), dtype=np.complex64),
                    1_000.0,
                )
            with self.assertRaisesRegex(ValueError, "finite and positive"):
                write_sigmf_recording(
                    root,
                    "bad-rate",
                    self.samples(),
                    float("inf"),
                )
            with self.assertRaisesRegex(ValueError, "plain filename"):
                write_sigmf_recording(
                    root,
                    "../escape",
                    self.samples(),
                    1_000.0,
                )
            with self.assertRaisesRegex(ValueError, "must be finite"):
                write_sigmf_recording(
                    root,
                    "nan-integer",
                    np.asarray([complex(float("nan"), 0)]),
                    1_000.0,
                    datatype="ci16_le",
                )
            with self.assertRaisesRegex(
                ValueError,
                "non-negative sample index",
            ):
                write_sigmf_recording(
                    root,
                    "negative-offset",
                    self.samples(),
                    1_000.0,
                    global_metadata={"core:offset": -1},
                )
            with self.assertRaisesRegex(ValueError, "within the recording"):
                write_sigmf_recording(
                    root,
                    "capture-before-offset",
                    self.samples(),
                    1_000.0,
                    global_metadata={"core:offset": 10},
                    captures=({"core:sample_start": 9},),
                )
            with self.assertRaisesRegex(ValueError, "sample ranges"):
                write_sigmf_recording(
                    root,
                    "annotation-after-end",
                    self.samples(),
                    1_000.0,
                    global_metadata={"core:offset": 10},
                    annotations=({
                        "core:sample_start": 13,
                        "core:sample_count": 2,
                    },),
                )

    def test_reader_uses_logical_offsets_and_active_capture_metadata(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            metadata_path, data_path = write_sigmf_recording(
                root / "one",
                "capture",
                self.samples(),
                2_000.0,
                datatype="ci16_le",
                global_metadata={"core:offset": 1_000},
                captures=(
                    {"core:sample_start": 1_003},
                    {"core:sample_start": 1_002, "core:frequency": 200.0},
                    {"core:sample_start": 1_000, "core:frequency": 100.0},
                ),
                annotations=(
                    {
                        "core:sample_start": 1_002,
                        "core:sample_count": 1,
                        "core:comment": "Later",
                    },
                    {
                        "core:sample_start": 1_000,
                        "core:sample_count": 1,
                        "core:comment": "Earlier",
                    },
                ),
            )
            write_sigmf_recording(
                root / "two",
                "capture",
                self.samples()[0],
                2_000.0,
            )
            payload = json.loads(metadata_path.read_text())
            self.assertEqual(
                [1_000, 1_002, 1_003],
                [
                    capture["core:sample_start"]
                    for capture in payload["captures"]
                ],
            )
            self.assertEqual(
                [1_000, 1_002],
                [
                    annotation["core:sample_start"]
                    for annotation in payload["annotations"]
                ],
            )

            recording = load_sigmf_recording(metadata_path)
            self.assertEqual(1_000, recording.sample_offset)
            self.assertEqual(self.samples().size * 4, data_path.stat().st_size)
            np.testing.assert_allclose(
                self.samples()[:, 1:3],
                recording.read(1, 2),
                atol=4e-5,
            )
            self.assertEqual(100.0, recording.center_frequency_at(1))
            self.assertEqual(200.0, recording.center_frequency_at(2))
            self.assertEqual(0.0, recording.center_frequency_at(3))

            created = add_sigmf_annotation(
                recording,
                1,
                2,
                AnnotationRequest(
                    0.0,
                    values={"comment": "Local coordinates"},
                ),
            )
            self.assertEqual(1 / recording.sample_rate, created.start_seconds)
            self.assertEqual(
                [1_000, 1_001, 1_002],
                [
                    annotation["core:sample_start"]
                    for annotation in json.loads(
                        metadata_path.read_text()
                    )["annotations"]
                ],
            )
            self.assertEqual(
                [0.0, 1 / 2_000.0, 2 / 2_000.0],
                [
                    annotation.start_seconds
                    for annotation in read_sigmf_annotations(recording)
                ],
            )

            resources = sigmf_source(root).discover()
            self.assertEqual(
                {"one::capture", "two::capture"},
                {resource.identifier for resource in resources},
            )
            self.assertEqual(
                {("one",), ("two",)},
                {resource.navigation_path for resource in resources},
            )

    def test_window_annotation_and_export_edges_are_safe(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            metadata_path, _ = write_sigmf_recording(
                root,
                "capture",
                self.samples(),
                1_000.0,
                datatype="ci16_le",
            )
            recording = load_sigmf_recording(metadata_path)
            window = SigMFWindow(recording, 1, recording.read(1, 2))
            self.assertEqual((2,), power_overview(recording, bins=2).shape)
            with self.assertRaisesRegex(ValueError, "channel-first"):
                SigMFWindow(recording, 0, recording.read(0, 2)[0])
            with self.assertRaisesRegex(ValueError, "extend beyond"):
                SigMFWindow(
                    recording,
                    3,
                    np.zeros((2, 2), dtype=np.complex64),
                )

            annotator = SigMFAnnotator(generator="Local plugin test")
            created = annotator.annotate(
                recording,
                window,
                AnnotationRequest(
                    0.0,
                    values={"comment": "Interesting"},
                ),
            )
            self.assertEqual((created,), annotator.discover(recording))
            before_invalid = metadata_path.read_text()
            with self.assertRaisesRegex(ValueError, "finite and increasing"):
                add_sigmf_annotation(
                    recording,
                    0,
                    1,
                    AnnotationRequest(
                        0.0,
                        values={"comment": "Invalid"},
                    ),
                    frequency_lower_hz=float("nan"),
                    frequency_upper_hz=10.0,
                )
            self.assertEqual(before_invalid, metadata_path.read_text())

            exported = SigMFExporter().export(
                recording,
                recording,
                ExportRequest("buffer", "json"),
                root / "exports",
            )
            self.assertEqual(
                recording.sample_count,
                json.loads(exported.read_text())["sample_count"],
                )

    def test_json_exports_round_trip_complex128_samples(self):
        with TemporaryDirectory() as directory:
            values = np.asarray([[
                complex(
                    np.nextafter(1.0, 2.0),
                    np.nextafter(-1.0, -2.0),
                ),
                complex(np.pi, np.e),
            ]], dtype=np.complex128)
            exported = write_array_bundle_export(
                directory,
                stem="precise",
                arrays={"samples": values},
                sample_rate=1_000.0,
                start_sample=0,
                scope="buffer",
                format="json",
            )
            payload = json.loads(exported.read_text())
            encoded = payload["samples"]["samples"]
            restored = np.asarray(encoded["real"]) + 1j * np.asarray(
                encoded["imag"]
            )
            np.testing.assert_array_equal(values, restored)

    def test_failed_exports_leave_no_partial_outputs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "non-finite"):
                write_sample_export(
                    root,
                    stem="samples",
                    samples=np.asarray([complex(float("nan"), 0)]),
                    sample_rate=1_000.0,
                    start_sample=0,
                    scope="buffer",
                    format="json",
                    metadata={},
                )
            self.assertEqual((), tuple(root.iterdir()))
            with self.assertRaisesRegex(ValueError, "plain filename"):
                write_sample_export(
                    root,
                    stem="../escape",
                    samples=self.samples(),
                    sample_rate=1_000.0,
                    start_sample=0,
                    scope="buffer",
                    format="json",
                    metadata={},
                )
            with self.assertRaisesRegex(ValueError, "conflict"):
                write_array_bundle_export(
                    root,
                    stem="bundle",
                    arrays={"sample_rate": self.samples()},
                    sample_rate=1_000.0,
                    start_sample=0,
                    scope="full",
                    format="mat",
                )


if __name__ == "__main__":
    unittest.main()
