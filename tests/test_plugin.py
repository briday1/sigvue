import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import plotly.graph_objects as go

from workspace_browser.plugin import AnalysisContext, AnalysisWorkspace, DataResource, DirectorySource


class ExampleSource:
    def discover(self):
        return [DataResource("recording", "Recording", source=[1, 2, 3, 4])]

    def open(self, resource):
        return resource.source


class PluginAuthoringTests(unittest.TestCase):
    def test_directory_source_creates_listing_rows_and_opens_selected_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first.dat").write_text("1,2,3", encoding="utf-8")
            (root / "second.dat").write_text("4,5,6", encoding="utf-8")
            (root / "ignored.txt").write_text("ignored", encoding="utf-8")

            def analyze(data, ui):
                with ui.tab("Values"):
                    ui.plot(go.Figure(go.Scatter(y=data)), key="values")

            workspace = AnalysisWorkspace(
                identifier="files",
                name="Files",
                description="Directory files",
                source=DirectorySource(root, pattern="*.dat", loader=lambda path: [int(value) for value in path.read_text().split(",")]),
                analyze=analyze,
            )
            self.assertEqual(["first.dat", "second.dat"], [item.identifier for item in workspace.discover_items()])
            figure = workspace.open_item("second.dat").page.views[0].callback({})
            self.assertEqual((4, 5, 6), figure.data[0].y)

    def test_source_and_analysis_are_adapted_to_workspace_contract(self):
        def analyze(data, ui: AnalysisContext):
            size = ui.select("size", default=2, options=(2, 4))
            ui.playback(duration=2.0, step=0.5)
            ui.stat("Samples", len(data))
            with ui.tab("Data", columns=2):
                ui.plot(go.Figure(go.Scatter(y=data[:size])), key="raw")
                ui.plot(go.Figure(go.Scatter(y=list(reversed(data[:size])))), key="reversed")

        workspace = AnalysisWorkspace(
            identifier="example",
            name="Example",
            description="Example analysis",
            source=ExampleSource(),
            analyze=analyze,
        )
        self.assertEqual(["recording"], [item.identifier for item in workspace.discover_items()])
        opened = workspace.open_item("recording")
        self.assertEqual("grid", opened.page.layout.kind)
        self.assertEqual(2, len(opened.page.views))
        self.assertEqual("seek", opened.page.playback.mode)
        self.assertEqual(4, opened.page.statistics["Samples"])
        self.assertRegex(opened.page.statistics["Analysis runtime"], r"^\d+\.\d ms$")
        figure = opened.page.views[0].callback({"size": "4"})
        self.assertIsInstance(figure, go.Figure)
        self.assertEqual((1, 2, 3, 4), figure.data[0].y)

    def test_delivery_policy_prepares_data_before_analysis(self):
        class WindowDelivery:
            def prepare(self, source_data, ui):
                size = ui.number("buffer_size", default=2, minimum=1)
                start = round(ui.playback(duration=2.0, step=1.0))
                return source_data[start : start + size]

        def analyze(window, ui):
            with ui.tab("Window"):
                ui.plot(go.Figure(go.Scatter(y=window)), key="window")

        workspace = AnalysisWorkspace(
            identifier="delivered",
            name="Delivered",
            description="Delivery policy",
            source=ExampleSource(),
            delivery=WindowDelivery(),
            analyze=analyze,
        )
        page = workspace.open_item_with_values("recording", {"buffer_size": "3", "__playback_time_seconds": "1"}).page
        self.assertEqual((2, 3, 4), page.views[0].callback({"buffer_size": "3", "__playback_time_seconds": "1"}).data[0].y)
        self.assertEqual("seek", page.playback.mode)
        self.assertEqual([2, 3, 4], page.export_callback({"buffer_size": "3", "__playback_time_seconds": "1"}))

    def test_windowed_delivery_receives_framework_selected_interval(self):
        class WindowedDelivery:
            def prepare(self, source_data, ui):
                start, end = ui.windowed(
                    duration=4.0,
                    default_window=2.0,
                    minimum_window=0.5,
                    step=0.25,
                    overview=(0.1, 0.8, 0.2, 1.0),
                    overview_label="Peak power",
                )
                return source_data[round(start) : round(end)]

        def analyze(window, ui):
            with ui.tab("Window"):
                ui.plot(go.Figure(go.Scatter(y=window)), key="window")

        workspace = AnalysisWorkspace(
            identifier="windowed",
            name="Windowed",
            description="Window selection",
            source=ExampleSource(),
            delivery=WindowedDelivery(),
            analyze=analyze,
        )
        values = {"__window_start_seconds": "1", "__window_end_seconds": "3"}
        page = workspace.open_item_with_values("recording", values).page
        self.assertEqual("windowed", page.playback.mode)
        self.assertEqual((1.0, 3.0), (page.playback.window_start_seconds, page.playback.window_end_seconds))
        self.assertEqual("Peak power", page.playback.overview_label)
        self.assertEqual((0.1, 0.8, 0.2, 1.0), page.playback.overview_values)
        self.assertEqual((2, 3), page.views[0].callback(values).data[0].y)
        self.assertEqual([2, 3], page.export_callback(values))

    def test_windowed_overview_rejects_nonfinite_statistics(self):
        ui = AnalysisContext({})
        with self.assertRaisesRegex(ValueError, "finite"):
            ui.windowed(duration=1.0, default_window=0.1, overview=(0.0, float("nan")))

    def test_windowed_overview_is_optional_and_independent_of_sample_count(self):
        ui = AnalysisContext({})
        self.assertEqual((0.0, 2.0), ui.windowed(duration=60.0, default_window=2.0))
        self.assertEqual((), ui.playback_config.overview_values)

        ui.windowed(duration=60.0, default_window=2.0, overview=(0.25,))
        self.assertEqual((0.25,), ui.playback_config.overview_values)

    def test_analysis_can_request_framework_live_refresh(self):
        def analyze(data, ui: AnalysisContext):
            ui.refresh(every=1.0)
            with ui.tab("Live"):
                ui.plot(go.Figure(go.Scatter(y=data)), key="live")

        workspace = AnalysisWorkspace(
            identifier="live",
            name="Live",
            description="Live analysis",
            source=ExampleSource(),
            analyze=analyze,
        )
        self.assertEqual(1.0, workspace.open_item("recording").page.refresh.interval_seconds)

    def test_numeric_inputs_return_typed_and_bounded_values(self):
        ui = AnalysisContext({"count": "12", "duration": "0.25"})
        self.assertEqual(10, ui.number("count", default=4, minimum=1, maximum=10, step=1))
        self.assertEqual(0.25, ui.number("duration", default=0.1, minimum=0.01, step=0.01))
        self.assertEqual(["integer", "float"], [control.control_type for control in ui.controls])

    def test_trace_style_returns_plotly_options_and_details_controls(self):
        ui = AnalysisContext(
            {
                "average_line_style": "dashdot",
                "average_marker": "diamond",
                "average_color": "#123abc",
                "average_width": "3.5",
            }
        )
        style = ui.trace_style("average", label="Average", color="#087e8b")

        self.assertEqual("lines+markers", style.mode)
        self.assertEqual({"color": "#123abc", "width": 3.5, "dash": "dashdot"}, style.line)
        self.assertEqual({"color": "#123abc", "symbol": "diamond"}, style.plotly_marker)
        self.assertEqual(["color", "float", "select", "select"], [control.control_type for control in ui.controls])
        self.assertTrue(all(control.placement == "details" for control in ui.controls))
        self.assertTrue(all(control.group == "Plot styles" for control in ui.controls))
        self.assertTrue(all(control.picker == "average" for control in ui.controls))
        self.assertTrue(all(control.picker_label == "Average" for control in ui.controls))
        self.assertEqual(["Color", "Line width", "Line style", "Marker"], [control.label for control in ui.controls])

        with self.assertRaisesRegex(ValueError, "#RRGGBB"):
            AnalysisContext({}).trace_style("invalid", color="teal")

    def test_inline_parameter_group_places_typed_controls_in_layout(self):
        def analyze(data, ui: AnalysisContext):
            with ui.tab("Parameterized"):
                with ui.parameter_group("Display parameters", columns=2):
                    threshold = ui.number("threshold", label="Threshold (dB)", default=2.5, step=0.1)
                    mode = ui.select("mode", label="Estimator", default="Mean", options=("Mean", "Maximum"))
                ui.plot(go.Figure(go.Scatter(y=[threshold], name=mode)), key="result")

        workspace = AnalysisWorkspace(
            identifier="inline-parameters",
            name="Inline parameters",
            description="Layout controls",
            source=ExampleSource(),
            analyze=analyze,
        )
        page = workspace.open_item_with_values("recording", {"threshold": "4.5", "mode": "Maximum"}).page
        self.assertEqual(["inline", "inline"], [control.placement for control in page.controls])
        self.assertEqual(["Threshold (dB)", "Estimator"], [control.label for control in page.controls])
        self.assertEqual("control_group", page.layout.children[0].kind)
        self.assertEqual({"label": "Display parameters", "columns": 2}, page.layout.children[0].props)
        self.assertEqual(["threshold", "mode"], [node.props["name"] for node in page.layout.children[0].children])
        figure = page.views[0].callback({"threshold": "4.5", "mode": "Maximum"})
        self.assertEqual((4.5,), figure.data[0].y)
        self.assertEqual("Maximum", figure.data[0].name)

    def test_analysis_can_publish_workflow_statistics(self):
        ui = AnalysisContext({})
        ui.stat("Intervals", 15)
        ui.stat("Buffer", "1.5 s")
        self.assertEqual({"Intervals": 15, "Buffer": "1.5 s"}, ui.statistics)

    def test_switcher_views_can_mix_framework_tables_and_plots(self):
        def analyze(data, ui: AnalysisContext):
            with ui.tab("Calibration"):
                with ui.switcher("Calibration", key="calibration"):
                    with ui.switcher_view("Phase", columns=(1, 2)):
                        ui.table([{"Channel": 1, "Offset": "0 deg"}], key="phase-table")
                        ui.plot(go.Figure(), key="phase-plot")
                    with ui.switcher_view("Noise", columns=(1, 2)):
                        with ui.group("column"):
                            with ui.parameter_group("Noise parameters"):
                                ui.number("noise_floor", default=-174.0)
                            ui.table([{"Channel": 1, "NF": "7 dB"}], key="noise-table")
                        ui.plot(go.Figure(), key="noise-plot")

        workspace = AnalysisWorkspace(
            identifier="mixed-switcher",
            name="Mixed switcher",
            description="Mixed switched layouts",
            source=ExampleSource(),
            analyze=analyze,
        )
        page = workspace.open_item("recording").page
        switcher = page.layout.children[0]
        self.assertEqual(["Phase", "Noise"], [choice.props["label"] for choice in switcher.children])
        self.assertEqual((1, 2), switcher.children[0].props["columns"])
        self.assertEqual(["view_slot", "view_slot"], [node.kind for node in switcher.children[0].children])
        self.assertEqual("inline", page.controls[0].placement)
        self.assertEqual(["control_group", "view_slot"], [node.kind for node in switcher.children[1].children[0].children])

    def test_analysis_can_add_multiple_button_and_dropdown_view_switchers(self):
        def figure(value):
            return go.Figure(go.Scatter(y=[value]))

        def analyze(data, ui: AnalysisContext):
            with ui.tab("Comparisons", columns=2):
                ui.view_switcher("Channel", {"One": figure(1), "Two": figure(2)}, key="channel", selector="buttons")
                ui.view_switcher("Metric", {"Raw": figure(3), "Filtered": figure(4)}, key="metric", selector="dropdown")

        workspace = AnalysisWorkspace(
            identifier="switchers",
            name="Switchers",
            description="Multiple local view selectors",
            source=ExampleSource(),
            analyze=analyze,
        )
        page = workspace.open_item("recording").page
        self.assertEqual(4, len(page.views))
        self.assertEqual(["buttons", "dropdown"], [node.props["selector"] for node in page.layout.children])

    def test_static_views_are_cached_while_dynamic_views_follow_playback(self):
        builds = {"static": 0}

        def static_figure():
            builds["static"] += 1
            return go.Figure(go.Scatter(y=[builds["static"]]))

        def analyze(data, ui: AnalysisContext):
            time = ui.playback(duration=2.0, step=0.5)
            with ui.tab("Calibration", update="static"):
                ui.plot(static_figure, key="calibration")
            with ui.tab("Playback"):
                ui.plot(go.Figure(go.Scatter(y=[time])), key="playback")

        workspace = AnalysisWorkspace(
            identifier="lifecycles",
            name="Lifecycles",
            description="Static and dynamic views",
            source=ExampleSource(),
            analyze=analyze,
        )
        initial = workspace.open_item("recording").page
        later = workspace.open_item_with_values("recording", {"__playback_time_seconds": "1.5"}).page
        self.assertEqual(["static", "dynamic"], [view.update_policy for view in initial.views])
        self.assertIs(initial.views[0].callback({}), later.views[0].callback({}))
        self.assertEqual(1, builds["static"])
        self.assertEqual((1.5,), later.views[1].callback({"__playback_time_seconds": "1.5"}).data[0].y)

    def test_unknown_view_update_policy_is_rejected(self):
        ui = AnalysisContext({})
        with self.assertRaisesRegex(ValueError, "static.*dynamic"):
            with ui.tab("Invalid", update="sometimes"):
                pass

    def test_tab_can_mix_text_tables_and_plots_in_weighted_groups(self):
        ui = AnalysisContext({})
        with ui.tab("Diagnostics", columns=(1, 2), update="static"):
            with ui.group("column"):
                ui.text("# Calibration", key="notes")
                ui.table([{"Channel": 1, "Offset": "+0.0°"}], key="offsets")
            with ui.group("column"):
                ui.plot(go.Figure(go.Scatter(y=[1, 2])), key="alignment")

        layout = ui.layout()
        self.assertEqual((1, 2), layout.props["columns"])
        self.assertEqual(["column", "column"], [child.kind for child in layout.children])
        self.assertEqual(["notes", "offsets"], [child.view for child in layout.children[0].children])
        self.assertEqual({"notes": "static", "offsets": "static", "alignment": "static"}, ui.figure_updates)


if __name__ == "__main__":
    unittest.main()
