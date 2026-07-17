import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from workspace_browser.core.plugin import AnalysisContext
from workspace_browser.rendering.dispatch import RenderKind, detect_render_kind
from examples.lfm_collection import create_workspace as create_buffered_workspace
from examples.lfm_full_recording import create_workspace as create_full_workspace
from examples.lfm_pipeline import (
    BufferedDelivery,
    CHANNEL_COLORS,
    CollectionMember,
    LfmCollection,
    LfmInput,
    WholeFileDelivery,
    _calibrate,
    _linear_average_db,
    _products,
    analyze_lfm,
)


class LfmDeliveryTests(unittest.TestCase):
    def test_workspace_entry_points_share_pipeline_and_change_only_delivery(self):
        buffered = create_buffered_workspace(Path("missing"))
        full = create_full_workspace(Path("missing"))
        self.assertIs(analyze_lfm, buffered.analyze)
        self.assertIs(analyze_lfm, full.analyze)
        self.assertIsInstance(buffered.delivery, BufferedDelivery)
        self.assertIsInstance(full.delivery, WholeFileDelivery)

    def test_whole_file_delivery_has_only_processing_prf_and_returns_all_samples(self):
        with TemporaryDirectory() as directory:
            collection = self._collection(Path(directory), sample_count=1_000)
            ui = AnalysisContext({})
            delivered = WholeFileDelivery(default_processing_prf_hz=100).prepare(collection, ui)
            self.assertEqual((4, 1_000), delivered.ota_counts.shape)
            self.assertEqual(10, delivered.pri_samples)
            self.assertEqual(["processing_prf_hz"], [control.name for control in ui.controls])
            self.assertEqual("static", ui.playback_config.mode)

    def test_buffered_delivery_owns_playback_and_returns_only_window(self):
        with TemporaryDirectory() as directory:
            collection = self._collection(Path(directory), sample_count=1_000)
            ui = AnalysisContext({"buffer_seconds": "0.1", "processing_prf_hz": "100", "__playback_time_seconds": "0.4"})
            delivered = BufferedDelivery().prepare(collection, ui)
            self.assertEqual((4, 100), delivered.ota_counts.shape)
            self.assertEqual(400, delivered.start_sample)
            self.assertEqual(10, delivered.pri_samples)
            self.assertEqual(["buffer_seconds", "processing_prf_hz", "seek_seconds", "refresh_seconds"], [control.name for control in ui.controls])
            self.assertEqual("live", ui.playback_config.mode)

    def test_buffered_delivery_can_expose_seek_without_live_controls(self):
        with TemporaryDirectory() as directory:
            collection = self._collection(Path(directory), sample_count=1_000)
            ui = AnalysisContext({"buffer_seconds": "0.1", "processing_prf_hz": "100"})
            BufferedDelivery(playback_mode="seek").prepare(collection, ui)
            self.assertEqual("seek", ui.playback_config.mode)

    def test_live_tail_rechecks_common_file_growth_and_preserves_historical_seek(self):
        with TemporaryDirectory() as directory:
            collection = self._collection(Path(directory), sample_count=1_000)
            live_values = {
                "buffer_seconds": "0.1",
                "processing_prf_hz": "100",
                "__playback_follow_live": "true",
            }
            initial_ui = AnalysisContext(live_values)
            initial = BufferedDelivery().prepare(collection, initial_ui)
            self.assertEqual(900, initial.start_sample)
            self.assertEqual(0.9, initial_ui.playback_config.duration_seconds)

            extra = np.zeros((200, 2), dtype="<i2")
            for member in collection.members["ota"]:
                with member.data_path.open("ab") as stream:
                    extra.tofile(stream)
            grown = BufferedDelivery().prepare(collection, AnalysisContext(live_values))
            self.assertEqual(1_100, grown.start_sample)

            historical = BufferedDelivery().prepare(
                collection,
                AnalysisContext({**live_values, "__playback_follow_live": "false", "__playback_time_seconds": "0.4"}),
            )
            self.assertEqual(400, historical.start_sample)

    def test_processing_prf_changes_whole_file_reshape_without_changing_data(self):
        with TemporaryDirectory() as directory:
            collection = self._collection(Path(directory), sample_count=1_000)
            delivered = WholeFileDelivery().prepare(collection, AnalysisContext({"processing_prf_hz": "50"}))
            self.assertEqual((4, 1_000), delivered.ota_counts.shape)
            self.assertEqual(20, delivered.pri_samples)

    def test_fractional_pri_start_offsets_fast_time_coordinates(self):
        channels = np.ones((4, 400), dtype=np.complex64)
        aligned = _products(channels, rate=1_000.0, pri=100, start=200)
        shifted = _products(channels, rate=1_000.0, pri=100, start=225)
        self.assertAlmostEqual(0.0, aligned.fast_time_us[0])
        self.assertAlmostEqual(25_000.0, shifted.fast_time_us[0])
        np.testing.assert_allclose(25_000.0, shifted.fast_time_us - aligned.fast_time_us)
        np.testing.assert_allclose(aligned.slow_time_s, shifted.slow_time_s)
        self.assertAlmostEqual(0.05, shifted.slow_time_s[0])

    def test_full_pri_psd_is_invariant_to_circular_fast_time_shift(self):
        pri = 1_024
        pulse = np.zeros(pri, dtype=np.complex64)
        pulse[:64] = np.exp(1j * 2 * np.pi * 0.125 * np.arange(64))
        baseline = np.tile(pulse, (4, 4))
        shifted = np.tile(np.roll(pulse, 700), (4, 4))

        baseline_products = _products(baseline, rate=1_024.0, pri=pri, start=0)
        shifted_products = _products(shifted, rate=1_024.0, pri=pri, start=0)

        self.assertEqual(512, baseline_products.frequencies_hz.size)
        np.testing.assert_allclose(
            baseline_products.psd_waterfall_dbm_hz,
            shifted_products.psd_waterfall_dbm_hz,
            atol=1e-10,
        )
        np.testing.assert_allclose(
            baseline_products.psd_mean_dbm_hz,
            shifted_products.psd_mean_dbm_hz,
            atol=1e-10,
        )
        np.testing.assert_allclose(
            baseline_products.psd_max_dbm_hz,
            shifted_products.psd_max_dbm_hz,
            atol=1e-10,
        )

    def test_combined_noise_reference_averages_linear_power_before_db(self):
        self.assertAlmostEqual(10 * np.log10(5.5), _linear_average_db(np.asarray([0.0, 10.0])))

    def test_noise_tab_exercises_inline_number_and_dropdown_parameters(self):
        samples = np.ones((4, 100), dtype=np.complex64) * (100 + 25j)
        data = LfmInput(
            sample_rate=1_000.0,
            calibration_dbm=-20.0,
            adc_bits=16,
            pri_samples=10,
            start_sample=0,
            calibration_counts=samples,
            noise_counts=samples * 0.01,
            ota_counts=samples,
        )
        baseline = AnalysisContext({"reference_noise_psd_dbm_hz": "-174", "adc_bits": "8"})
        changed = AnalysisContext({"reference_noise_psd_dbm_hz": "-168.5", "adc_bits": "16"})
        analyze_lfm(data, baseline)
        analyze_lfm(data, changed)

        inline = [control for control in changed.controls if control.placement == "inline"]
        self.assertEqual(
            ["phase_reference", "amplitude_reference", "adc_bits", "reference_noise_psd_dbm_hz"],
            [control.name for control in inline],
        )
        self.assertEqual(
            ("Channel 1", "Channel 2", "Channel 3", "Channel 4", "Min"),
            next(control for control in inline if control.name == "amplitude_reference").options,
        )
        self.assertEqual("Min", next(control for control in inline if control.name == "amplitude_reference").default)
        self.assertEqual(["Waterfall", "Time Domain", "Frequency Domain", "Calibration"], [tab.label for tab in changed.tabs])
        switcher = changed.tabs[-1].nodes[0]
        self.assertEqual("view_switcher", switcher.kind)
        self.assertEqual(["Phase", "Amplitude", "Noise"], [node.props["label"] for node in switcher.children])
        self.assertTrue(all(node.kind == "grid" for node in switcher.children))
        self.assertEqual(["column", "view_slot"], [child.kind for child in switcher.children[0].children])
        self.assertEqual(["column", "view_slot"], [child.kind for child in switcher.children[1].children])
        self.assertEqual(["column", "view_slot"], [child.kind for child in switcher.children[2].children])
        self.assertFalse(any(trace.type == "table" for key, figure in changed.figures.items() if key.endswith("-plot") for trace in figure.data))

        baseline_nf = float(baseline.figures["noise-diagnostics"][0]["Estimated NF"].split()[0])
        changed_nf = float(changed.figures["noise-diagnostics"][0]["Estimated NF"].split()[0])
        self.assertAlmostEqual(5.5, baseline_nf - changed_nf)
        baseline_full_scale = float(baseline.figures["amplitude-diagnostics"][0]["Recorded full-scale power"].split()[0])
        changed_full_scale = float(changed.figures["amplitude-diagnostics"][0]["Recorded full-scale power"].split()[0])
        self.assertAlmostEqual(48.23, changed_full_scale - baseline_full_scale, delta=0.02)
        self.assertEqual(
            {"Channel", "Normalization", "Recorded full-scale power"},
            set(changed.figures["amplitude-diagnostics"][0]),
        )
        self.assertEqual(3, len(changed.figures["amplitude-summary"].splitlines()))
        self.assertIn("Normalized to: **Min (Channel 1)**", changed.figures["amplitude-summary"])
        self.assertEqual(RenderKind.MARKDOWN, detect_render_kind(changed.figures["amplitude-summary"]))
        self.assertIn("Calibrated scale:", changed.figures["amplitude-summary"])
        self.assertIn("Calibrated full scale:", changed.figures["amplitude-summary"])
        names = [trace.name or "" for trace in changed.figures["noise-plot"].data]
        self.assertNotIn("Expected noise PSD", names)
        self.assertEqual(4, sum("measured floor" in name for name in names))
        self.assertEqual(
            "Reference noise PSD (dBm/Hz)",
            next(control for control in inline if control.name == "reference_noise_psd_dbm_hz").label,
        )

        self.assertEqual(4, len(set(CHANNEL_COLORS)))
        self.assertFalse(any(control.name.startswith("channel_") for control in changed.controls))
        style_controls = [control for control in changed.controls if control.group == "Plot styles"]
        self.assertEqual(16, len(style_controls))
        self.assertEqual({"mean_trace", "max_trace", "noise_trace", "full_scale_trace"}, {control.picker for control in style_controls})
        self.assertEqual("#087e8b", next(control for control in style_controls if control.name == "mean_trace_color").default)
        self.assertEqual("#d35d35", next(control for control in style_controls if control.name == "max_trace_color").default)

        for key in ("time-view-0", "frequency-view-0"):
            self.assert_axes_share_range(changed.figures[key], "x")
        for tab_index in (1, 2):
            switcher = changed.tabs[tab_index].nodes[0]
            self.assertEqual("view_switcher", switcher.kind)
            self.assertEqual(
                ["Multi", "Combined max", "Combined mean"],
                [choice.props["label"] for choice in switcher.children],
            )
        for key in ("time-view-1", "time-view-2", "frequency-view-1", "frequency-view-2"):
            self.assertEqual(6, len(changed.figures[key].data))
            self.assertEqual(1, len(list(changed.figures[key].select_xaxes())))
            self.assertEqual(CHANNEL_COLORS[0], changed.figures[key].data[0].line.color)
            self.assertEqual("Full scale", changed.figures[key].data[-1].name)
            self.assertEqual(1, sum(trace.name == "Full scale" for trace in changed.figures[key].data))
        for key in ("time-view-1", "time-view-2"):
            self.assertEqual("Average noise power", changed.figures[key].data[-2].name)
        for key in ("frequency-view-1", "frequency-view-2"):
            self.assertEqual("Average noise PSD", changed.figures[key].data[-2].name)
        for key in ("waterfall-domain-0", "waterfall-domain-1"):
            self.assert_axes_share_range(changed.figures[key], "x")
            self.assert_axes_share_range(changed.figures[key], "y")
        for key in (
            "phase-plot",
            "amplitude-plot",
            "noise-plot",
            "waterfall-domain-0",
            "waterfall-domain-1",
            "time-view-0",
            "time-view-1",
            "time-view-2",
            "frequency-view-0",
            "frequency-view-1",
            "frequency-view-2",
        ):
            self.assertTrue(all(axis.mirror is True for axis in changed.figures[key].select_xaxes()))
            self.assertTrue(all(axis.mirror is True for axis in changed.figures[key].select_yaxes()))

    def test_calibration_reference_channel_controls_phase_and_amplitude_normalization(self):
        count = 128
        phases = np.asarray([0.1, 0.4, -0.7, 1.2])
        amplitudes = np.asarray([100.0, 80.0, 60.0, 120.0])
        tone = amplitudes[:, None] * np.exp(1j * phases[:, None]) * np.ones((4, count))
        data = LfmInput(
            sample_rate=1_000.0,
            calibration_dbm=-20.0,
            adc_bits=16,
            pri_samples=16,
            start_sample=0,
            calibration_counts=tone.astype(np.complex64),
            noise_counts=np.ones((4, count), dtype=np.complex64),
            ota_counts=tone.astype(np.complex64),
        )

        selected = _calibrate(data, phase_reference="Channel 3", amplitude_reference="Channel 2")
        self.assertEqual(2, selected.phase_reference_channel)
        self.assertAlmostEqual(0.0, selected.phase_offsets[2], places=6)
        self.assertEqual(1, selected.amplitude_reference_channel)
        self.assertAlmostEqual(1.0, selected.amplitude_corrections[1])
        self.assertGreater(selected.amplitude_corrections[2], 1.0)

        minimum = _calibrate(data, amplitude_reference="Min")
        self.assertEqual(2, minimum.amplitude_reference_channel)
        self.assertEqual("Min (Channel 3)", minimum.amplitude_reference_label)
        self.assertAlmostEqual(1.0, minimum.amplitude_corrections[2])
        self.assertTrue(np.all(minimum.amplitude_corrections <= 1.0))

    def assert_axes_share_range(self, figure, dimension: str) -> None:
        references = []
        for index in range(1, 5):
            suffix = "" if index == 1 else str(index)
            axis = getattr(figure.layout, f"{dimension}axis{suffix}")
            references.append(axis.matches or f"{dimension}{suffix}")
        self.assertEqual(1, len(set(references)))

    @staticmethod
    def _collection(root: Path, *, sample_count: int) -> LfmCollection:
        members = {}
        for role in ("calibration", "terminated-noise", "ota"):
            records = []
            for channel in range(1, 5):
                path = root / f"{role}-{channel}.sigmf-data"
                iq = np.empty((sample_count, 2), dtype="<i2")
                iq[:, 0] = channel
                iq[:, 1] = -channel
                iq.tofile(path)
                records.append(CollectionMember(role, channel, root / "unused.sigmf-meta", path, 1.0))
            members[role] = tuple(records)
        return LfmCollection(1_000.0, -20.0, 16, members)


if __name__ == "__main__":
    unittest.main()
