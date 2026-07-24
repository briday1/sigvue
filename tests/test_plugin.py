import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import plotly.graph_objects as go

from sigvue.core.plugin import AnalysisContext
from sigvue.plugin import (
    Analysis,
    Annotator,
    Delivery,
    DataResource,
    DeliveryContext,
    DiscoveryColumn,
    DirectorySource,
    Presentation,
    Exporter,
    Segment,
    Source,
    ViewContext,
    Workspace,
)


class ExampleSource(Source[list[int]]):
    def discover(self):
        return [DataResource("recording", "Recording", source=[1, 2, 3, 4])]

    def open(self, resource):
        return resource.source


def no_parameters(data, ui):
    return None


def identity_process(data, settings):
    return data


def analysis_object(process, configure=None):
    if configure is None:
        class TestAnalysis(Analysis):
            def process(self, data, settings):
                return process(data, settings)

        return TestAnalysis()

    class ConfiguredTestAnalysis(Analysis):
        def configure(self, data, ui):
            return configure(data, ui)

        def process(self, data, settings):
            return process(data, settings)

    return ConfiguredTestAnalysis()


def presentation_object(present):
    class TestPresentation(Presentation):
        def present(self, products, ui):
            present(products, ui)

    return TestPresentation()


def make_workspace(*, process, present, configure=None, **kwargs):
    return Workspace(
        **kwargs,
        analysis=analysis_object(process, configure),
        presentation=presentation_object(present),
    )


class PluginAuthoringTests(unittest.TestCase):
    def test_lifecycle_context_protocols_expose_only_their_public_stage(self):
        self.assertTrue(hasattr(DeliveryContext, "number"))
        self.assertTrue(hasattr(DeliveryContext, "windowed"))
        self.assertFalse(hasattr(DeliveryContext, "plot"))
        self.assertTrue(hasattr(ViewContext, "plot"))
        self.assertFalse(hasattr(ViewContext, "playback"))

    def test_workspace_uses_only_the_split_lifecycle_contract(self):
        with self.assertRaisesRegex(TypeError, "unexpected keyword argument 'analyze'"):
            Workspace(
                identifier="legacy",
                name="Legacy",
                description="Old contract",
                source=ExampleSource(),
                analysis=analysis_object(identity_process),
                presentation=presentation_object(lambda data, ui: None),
                analyze=lambda data, ui: None,
            )

    def test_configure_process_and_present_exchange_typed_values(self):
        calls = {"configure": 0, "process": 0, "present": 0}

        def configure(data, ui):
            calls["configure"] += 1
            self.assertEqual([1, 2, 3, 4], data)
            return {"gain": float(ui.number("gain", default=2.0))}

        def process(data, settings):
            calls["process"] += 1
            return tuple(value * settings["gain"] for value in data)

        def present(products, ui):
            calls["present"] += 1
            color = str(ui.select("color", default="blue", options=("blue", "red")))
            with ui.tab("Signal"):
                ui.plot(go.Figure(go.Scatter(y=products, line={"color": color})), key="signal")

        workspace = make_workspace(
            identifier="split",
            name="Split",
            description="Split lifecycle",
            source=ExampleSource(),
            configure=configure,
            process=process,
            present=present,
        )
        opened = workspace.open_item("recording")
        recolored = opened.page.views[0].callback({"color": "red"})
        recomputed = opened.page.views[0].callback({"gain": "3", "color": "red"})

        self.assertEqual((2.0, 4.0, 6.0, 8.0), opened.page.views[0].callback({}).data[0].y)
        self.assertEqual("red", recolored.data[0].line.color)
        self.assertEqual((3.0, 6.0, 9.0, 12.0), recomputed.data[0].y)
        self.assertEqual(2, calls["process"])
        self.assertGreaterEqual(calls["configure"], 3)
        self.assertGreaterEqual(calls["present"], 3)

    def test_timeline_selection_is_part_of_the_process_cache_key(self):
        calls = {"process": 0}

        class PlaybackDelivery(Delivery[list[int], list[int]]):
            def prepare(self, source_data, ui):
                start = round(ui.playback(duration=3.0, step=1.0))
                return source_data[start : start + 1]

        def process(data, settings):
            calls["process"] += 1
            self.assertIsNone(settings)
            return tuple(data)

        def present(products, ui):
            with ui.tab("Signal"):
                ui.plot(go.Figure(go.Scatter(y=products)), key="signal")

        workspace = make_workspace(
            identifier="timeline-cache",
            name="Timeline cache",
            description="Timeline cache",
            source=ExampleSource(),
            delivery=PlaybackDelivery(),
            process=process,
            present=present,
        )
        opened = workspace.open_item("recording")
        later = opened.page.views[0].callback({"__playback_time_seconds": "1"})
        self.assertEqual((2,), later.data[0].y)
        self.assertEqual(2, calls["process"])
        self.assertEqual("not configured", opened.page.statistics["Configuration runtime"])

    def test_configured_parameters_can_be_placed_in_a_view(self):
        def configure(data, ui):
            return ui.number("threshold", label="Threshold", default=1.0)

        def present(products, ui):
            with ui.tab("Signal"):
                ui.place_parameters("threshold", label="Processing", columns=2)
                ui.plot(go.Figure(go.Scatter(y=[products])), key="signal")

        workspace = make_workspace(
            identifier="placed",
            name="Placed",
            description="Placed parameters",
            source=ExampleSource(),
            configure=configure,
            process=lambda data, threshold: threshold,
            present=present,
        )
        page = workspace.open_item("recording").page
        self.assertEqual("inline", page.controls[0].placement)
        self.assertEqual("control_group", page.layout.children[0].kind)
        self.assertEqual("threshold", page.layout.children[0].children[0].props["name"])

    def test_discovery_columns_are_typed_and_unique(self):
        column = DiscoveryColumn("sample_rate", "Sampling rate", "si", unit="sample/s")
        self.assertEqual("sample_rate", column.key)
        with self.assertRaisesRegex(ValueError, "Only SI"):
            DiscoveryColumn("date", "Date", "datetime", unit="s")
        with self.assertRaisesRegex(ValueError, "keys must be unique"):
            make_workspace(
                identifier="duplicate-columns",
                name="Duplicate columns",
                description="test",
                source=ExampleSource(),
                configure=no_parameters,
                process=identity_process,
                present=lambda data, ui: None,
                discovery_columns=(column, column),
            )

    def test_public_source_and_delivery_are_explicit_framework_objects(self):
        class TypedDelivery(Delivery[list[int], tuple[int, ...]]):
            def prepare(self, source_data: list[int], ui: AnalysisContext) -> tuple[int, ...]:
                return tuple(source_data)

        self.assertIsInstance(ExampleSource(), Source)
        self.assertIsInstance(TypedDelivery(), Delivery)

        class IncompleteDelivery(Delivery[list[int], tuple[int, ...]]):
            pass

        with self.assertRaisesRegex(TypeError, r"abstract method prepare"):
            IncompleteDelivery()

    def test_source_and_delivery_subclasses_form_an_explicit_pipeline(self):
        resource = DataResource("one", "One", source=5)

        class OneSource(Source[int]):
            def discover(self):
                return (resource,)

            def open(self, selected):
                return selected.source

        class DoubleDelivery(Delivery[int, int]):
            def prepare(self, value, ui):
                return value * 2

        def present(value, ui):
            with ui.tab("Value"):
                ui.text(str(value), key="value")

        workspace = Workspace(
            identifier="callbacks",
            name="Callbacks",
            description="Callback-backed framework objects",
            source=OneSource(),
            delivery=DoubleDelivery(),
            analysis=analysis_object(lambda value, settings: value + 1),
            presentation=presentation_object(present),
        )
        self.assertEqual(["one"], [item.identifier for item in workspace.discover_items()])
        opened = workspace.open_item("one")
        self.assertEqual("11", opened.page.views[0].callback({}))

    def test_workspace_rejects_structural_analysis_and_presentation_lookalikes(self):
        class LooksLikeAnalysis:
            process = staticmethod(identity_process)

        with self.assertRaisesRegex(TypeError, "analysis must be an Analysis object"):
            Workspace(
                identifier="lookalike-analysis",
                name="Lookalike",
                description="Not a framework object",
                source=ExampleSource(),
                analysis=LooksLikeAnalysis(),
                presentation=presentation_object(lambda value, ui: None),
            )

        with self.assertRaisesRegex(TypeError, "presentation must be a Presentation object"):
            Workspace(
                identifier="lookalike-presentation",
                name="Lookalike",
                description="Not a framework object",
                source=ExampleSource(),
                analysis=analysis_object(identity_process),
                presentation=object(),
            )

        class LooksLikeAnnotator:
            fields = ()

            def discover(self, source_data):
                return ()

            def annotate(self, source_data, delivered_data, request):
                return None

        with self.assertRaisesRegex(TypeError, "annotator must be an Annotator object"):
            Workspace(
                identifier="lookalike-annotator",
                name="Lookalike",
                description="Not a framework object",
                source=ExampleSource(),
                analysis=analysis_object(identity_process),
                presentation=presentation_object(lambda value, ui: None),
                annotator=LooksLikeAnnotator(),
            )

        class LooksLikeExporter:
            scopes = ()
            formats = ()

            def export(self, source_data, delivered_data, request, directory):
                return directory

        with self.assertRaisesRegex(TypeError, "exporter must be an Exporter object"):
            Workspace(
                identifier="lookalike-exporter",
                name="Lookalike",
                description="Not a framework object",
                source=ExampleSource(),
                analysis=analysis_object(identity_process),
                presentation=presentation_object(lambda value, ui: None),
                exporter=LooksLikeExporter(),
            )

        class MissingAnnotationMethods(Annotator):
            pass

        class MissingExportMethods(Exporter):
            pass

        with self.assertRaisesRegex(TypeError, "abstract method"):
            MissingAnnotationMethods()
        with self.assertRaisesRegex(TypeError, "abstract method"):
            MissingExportMethods()

    def test_workspace_rejects_incomplete_contracts_with_actionable_errors(self):
        class MissingOpen:
            def discover(self):
                return []

        with self.assertRaisesRegex(TypeError, r"source must be a Source object"):
            make_workspace(
                identifier="invalid-source",
                name="Invalid source",
                description="Missing open",
                source=MissingOpen(),
                configure=no_parameters,
                process=identity_process,
                present=lambda data, ui: None,
            )

        with self.assertRaisesRegex(TypeError, r"delivery must be a Delivery object"):
            make_workspace(
                identifier="invalid-delivery",
                name="Invalid delivery",
                description="Missing prepare",
                source=ExampleSource(),
                delivery=object(),
                configure=no_parameters,
                process=identity_process,
                present=lambda data, ui: None,
            )

        class MissingProcess(Analysis):
            pass

        class MissingPresent(Presentation):
            pass

        with self.assertRaisesRegex(TypeError, r"abstract method process"):
            MissingProcess()
        with self.assertRaisesRegex(TypeError, r"abstract method present"):
            MissingPresent()

    def test_source_discovery_validates_resources_and_unique_identifiers(self):
        class InvalidResources(Source[object]):
            def discover(self):
                return ["not a resource"]

            def open(self, resource):
                return resource

        invalid = make_workspace(
            identifier="invalid-resources",
            name="Invalid resources",
            description="Wrong discovery value",
            source=InvalidResources(),
            configure=no_parameters,
            process=identity_process,
            present=lambda data, ui: None,
        )
        with self.assertRaisesRegex(TypeError, r"item 0 must be DataResource, got str"):
            invalid.discover_items()

        class DuplicateResources(Source[object]):
            def discover(self):
                return [DataResource("same", "First", 1), DataResource("same", "Second", 2)]

            def open(self, resource):
                return resource.source

        duplicate = make_workspace(
            identifier="duplicate-resources",
            name="Duplicate resources",
            description="Duplicate discovery identifiers",
            source=DuplicateResources(),
            configure=no_parameters,
            process=identity_process,
            present=lambda data, ui: None,
        )
        with self.assertRaisesRegex(ValueError, r"duplicate identifiers: same"):
            duplicate.discover_items()

    def test_directory_source_creates_listing_rows_and_opens_selected_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first.dat").write_text("1,2,3", encoding="utf-8")
            (root / "second.dat").write_text("4,5,6", encoding="utf-8")
            (root / "ignored.txt").write_text("ignored", encoding="utf-8")

            def analyze(data, ui):
                with ui.tab("Values"):
                    ui.plot(go.Figure(go.Scatter(y=data)), key="values")

            workspace = make_workspace(
                identifier="files",
                name="Files",
                description="Directory files",
                source=DirectorySource(root, pattern="*.dat", loader=lambda path: [int(value) for value in path.read_text().split(",")]),
                configure=no_parameters,
                process=identity_process,
                present=analyze,
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

        workspace = make_workspace(
            identifier="example",
            name="Example",
            description="Example analysis",
            source=ExampleSource(),
            configure=no_parameters,
            process=identity_process,
            present=analyze,
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
        class WindowDelivery(Delivery[list[int], list[int]]):
            def prepare(self, source_data, ui):
                size = ui.number("buffer_size", default=2, minimum=1)
                start = round(ui.playback(duration=2.0, step=1.0))
                return source_data[start : start + size]

        def analyze(window, ui):
            with ui.tab("Window"):
                ui.plot(go.Figure(go.Scatter(y=window)), key="window")

        workspace = make_workspace(
            identifier="delivered",
            name="Delivered",
            description="Delivery policy",
            source=ExampleSource(),
            delivery=WindowDelivery(),
            configure=no_parameters,
            process=identity_process,
            present=analyze,
        )
        page = workspace.open_item_with_values("recording", {"buffer_size": "3", "__playback_time_seconds": "1"}).page
        self.assertEqual((2, 3, 4), page.views[0].callback({"buffer_size": "3", "__playback_time_seconds": "1"}).data[0].y)
        self.assertEqual("seek", page.playback.mode)
        self.assertIsNone(page.export)

    def test_windowed_delivery_receives_framework_selected_interval(self):
        class WindowedDelivery(Delivery[list[int], list[int]]):
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

        workspace = make_workspace(
            identifier="windowed",
            name="Windowed",
            description="Window selection",
            source=ExampleSource(),
            delivery=WindowedDelivery(),
            configure=no_parameters,
            process=identity_process,
            present=analyze,
        )
        values = {"__window_start_seconds": "1", "__window_end_seconds": "3"}
        page = workspace.open_item_with_values("recording", values).page
        self.assertEqual("windowed", page.playback.mode)
        self.assertEqual((1.0, 3.0), (page.playback.window_start_seconds, page.playback.window_end_seconds))
        self.assertEqual("Peak power", page.playback.overview_label)
        self.assertEqual((0.1, 0.8, 0.2, 1.0), page.playback.overview_values)
        self.assertEqual((2, 3), page.views[0].callback(values).data[0].y)
        self.assertIsNone(page.export)

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

    def test_windowed_overview_can_follow_a_view_switcher(self):
        ui = AnalysisContext({})
        ui.windowed(
            duration=60.0,
            default_window=2.0,
            overview_series=((0.1, 0.2), (0.8, 0.9)),
            overview_durations=(10.0, 60.0),
            overview_switcher="channel",
        )
        self.assertEqual((0.1, 0.2), ui.playback_config.overview_values)
        self.assertEqual(((0.1, 0.2), (0.8, 0.9)), ui.playback_config.overview_series)
        self.assertEqual((10.0, 60.0), ui.playback_config.overview_durations_seconds)
        self.assertEqual("channel", ui.playback_config.overview_switcher_key)

    def test_windowed_overview_rejects_ambiguous_single_and_switched_values(self):
        ui = AnalysisContext({})
        with self.assertRaisesRegex(ValueError, "not both"):
            ui.windowed(
                duration=60.0,
                default_window=2.0,
                overview=(0.1, 0.2),
                overview_series=((0.1, 0.2), (0.8, 0.9)),
                overview_switcher="channel",
            )

    def test_windowed_overview_series_requires_a_view_switcher(self):
        ui = AnalysisContext({})
        with self.assertRaisesRegex(ValueError, "switcher"):
            ui.windowed(
                duration=60.0,
                default_window=2.0,
                overview_series=((0.1, 0.2), (0.8, 0.9)),
            )

    def test_windowed_overview_durations_must_match_the_series(self):
        ui = AnalysisContext({})
        with self.assertRaisesRegex(ValueError, "match"):
            ui.windowed(
                duration=60.0,
                default_window=2.0,
                overview_series=((0.1, 0.2), (0.8, 0.9)),
                overview_durations=(10.0,),
                overview_switcher="channel",
            )

    def test_pipeline_can_choose_timeline_display_units_without_changing_seconds(self):
        playback = AnalysisContext({"__playback_time_seconds": "7200"})
        self.assertEqual(7200.0, playback.playback(duration=172800.0, step=60.0, time_unit="h"))
        self.assertEqual("h", playback.playback_config.time_unit)
        self.assertEqual(172800.0, playback.playback_config.duration_seconds)

        windowed = AnalysisContext({"__window_start_seconds": "0.0000001", "__window_end_seconds": "0.0000003"})
        self.assertEqual(
            (1e-7, 3e-7),
            windowed.windowed(duration=1e-6, default_window=2e-7, minimum_window=1e-8, time_unit="ns"),
        )
        self.assertEqual("ns", windowed.playback_config.time_unit)
        samples = AnalysisContext({})
        self.assertEqual(
            (0.0, 256.0),
            samples.windowed(
                duration=4096,
                default_window=256,
                minimum_window=16,
                step=16,
                time_unit="samples",
            ),
        )
        self.assertEqual("samples", samples.playback_config.time_unit)

        with self.assertRaisesRegex(ValueError, "display unit"):
            AnalysisContext({}).playback(duration=1.0, time_unit="fortnight")

    def test_segmented_selects_irregular_predefined_interval(self):
        ui = AnalysisContext({"__segment_id": "late"})
        selected = ui.segmented(
            duration=20.0,
            segments=(
                Segment("late", 13.25, 0.8, "Late event"),
                Segment("early", 1.5, 0.2, "Early event"),
            ),
        )
        self.assertEqual("late", selected.identifier)
        self.assertEqual(["early", "late"], [segment.identifier for segment in ui.playback_config.segments])
        self.assertEqual("segmented", ui.playback_config.mode)

    def test_segmented_can_generate_regular_intervals_with_skips(self):
        ui = AnalysisContext({"__segment_id": "segment-3"})
        selected = ui.segmented(duration=10.0, segment_duration=1.0, stride=3.0)
        self.assertEqual((0.0, 3.0, 6.0, 9.0), tuple(segment.start_seconds for segment in ui.playback_config.segments))
        self.assertEqual("segment-3", selected.identifier)

    def test_analysis_can_request_framework_live_refresh(self):
        def analyze(data, ui: AnalysisContext):
            ui.refresh(every=1.0)
            with ui.tab("Live"):
                ui.plot(go.Figure(go.Scatter(y=data)), key="live")

        workspace = make_workspace(
            identifier="live",
            name="Live",
            description="Live analysis",
            source=ExampleSource(),
            configure=no_parameters,
            process=identity_process,
            present=analyze,
        )
        self.assertEqual(1.0, workspace.open_item("recording").page.refresh.interval_seconds)

    def test_numeric_inputs_return_typed_and_bounded_values(self):
        ui = AnalysisContext({"count": "12", "duration": "0.25"})
        self.assertEqual(10, ui.number("count", default=4, minimum=1, maximum=10, step=1))
        self.assertEqual(0.25, ui.number("duration", default=0.1, minimum=0.01, step=0.01))
        self.assertEqual(["integer", "float"], [control.control_type for control in ui.controls])

    def test_render_points_declares_an_explicit_user_control(self):
        ui = AnalysisContext({"frequency_points": "96"})

        value = ui.render_points(
            "frequency_points",
            default=256,
            minimum=16,
            maximum=4096,
            step=16,
            label="Frequency points",
        )

        self.assertEqual(96, value)
        control = ui.controls[0]
        self.assertEqual("integer", control.control_type)
        self.assertEqual("Rendering resolution", control.group)
        self.assertEqual((16, 4096, 16), (control.minimum, control.maximum, control.step))

    def test_render_points_rejects_invalid_bounds(self):
        ui = AnalysisContext({})
        with self.assertRaisesRegex(ValueError, "positive"):
            ui.render_points("points", default=0)
        with self.assertRaisesRegex(ValueError, "maximum"):
            ui.render_points("points", default=64, minimum=64, maximum=32)

    def test_toggle_returns_boolean_and_declares_switch_control(self):
        ui = AnalysisContext({"annotations": "false"})
        self.assertFalse(ui.toggle("annotations", default=True, label="Show annotations"))
        self.assertEqual("toggle", ui.controls[0].control_type)
        self.assertEqual("Show annotations", ui.controls[0].label)

    def test_trace_style_returns_plotly_options_and_details_controls(self):
        ui = AnalysisContext(
            {
                "average_line_style": "dashdot",
                "average_marker": "diamond",
                "average_color": "#123abc",
                "average_width": "3.5",
                "average_opacity": "0.4",
            }
        )
        style = ui.trace_style("average", label="Average", color="#087e8b")

        self.assertEqual("lines+markers", style.mode)
        self.assertEqual({"color": "rgba(18,58,188,0.4)", "width": 3.5, "dash": "dashdot"}, style.line)
        self.assertEqual({"color": "rgba(18,58,188,0.4)", "symbol": "diamond"}, style.plotly_marker)
        self.assertEqual("rgba(255,255,255,0.4)", style.color_with_opacity("#ffffff"))
        self.assertEqual(["color", "float", "float", "select", "select"], [control.control_type for control in ui.controls])
        self.assertTrue(all(control.placement == "details" for control in ui.controls))
        self.assertTrue(all(control.group == "Plot styles" for control in ui.controls))
        self.assertTrue(all(control.picker == "average" for control in ui.controls))
        self.assertTrue(all(control.picker_label == "Average" for control in ui.controls))
        self.assertEqual(["Color", "Line width", "Opacity", "Line style", "Marker"], [control.label for control in ui.controls])

        with self.assertRaisesRegex(ValueError, "#RRGGBB"):
            AnalysisContext({}).trace_style("invalid", color="teal")
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            AnalysisContext({}).trace_style("invalid", opacity=1.1)

    def test_colormap_picker_returns_choice_and_serializes_gradient_previews(self):
        ui = AnalysisContext({"waterfall_colormap": "Cividis"})
        selected = ui.colormap(
            "waterfall_colormap",
            label="Waterfall",
            default="Plasma",
            options=("Plasma", "Viridis", "Cividis"),
        )

        self.assertEqual("Cividis", selected)
        control = ui.controls[0]
        self.assertEqual("colormap", control.control_type)
        self.assertEqual(("Plasma", "Viridis", "Cividis"), control.options)
        self.assertEqual("#0d0887 0%", control.option_previews[0][0])
        self.assertEqual("#f0f921 100%", control.option_previews[0][-1])
        self.assertEqual("Plot styles", control.group)

        with self.assertRaisesRegex(ValueError, "default colormap"):
            AnalysisContext({}).colormap("invalid", default="Plasma", options=("Viridis",))

    def test_select_matches_mixed_numeric_options_posted_as_text(self):
        options = (60, 120, 230, 459.54)
        selected = AnalysisContext({"display_radius": "459.54"}).select(
            "display_radius", default=120, options=options,
        )
        invalid = AnalysisContext({"display_radius": "not-an-option"}).select(
            "display_radius", default=120, options=options,
        )

        self.assertEqual(459.54, selected)
        self.assertIsInstance(selected, float)
        self.assertEqual(120, invalid)

    def test_details_group_assigns_a_generic_sidebar_group(self):
        ui = AnalysisContext({})
        with ui.details_group("Raster rendering"):
            ui.select("width", default=1024, options=(512, 1024))
            ui.select("method", default="mean", options=("max", "mean", "median"))
        self.assertEqual(
            ["Raster rendering", "Raster rendering"],
            [control.group for control in ui.controls],
        )
        with self.assertRaisesRegex(RuntimeError, "cannot be nested"):
            with ui.details_group("Outer"):
                with ui.details_group("Inner"):
                    pass

    def test_limits_picker_returns_bounded_ordered_pair(self):
        ui = AnalysisContext({"dbfs_limits": "-82,-14"})
        selected = ui.limits(
            "dbfs_limits",
            label="dBFS scale",
            default=(-90, -20),
            minimum=-120,
            maximum=0,
            step=1,
        )

        self.assertEqual((-82.0, -14.0), selected)
        control = ui.controls[0]
        self.assertEqual("limits", control.control_type)
        self.assertEqual((-90.0, -20.0), control.default)
        self.assertEqual((-120.0, 0.0, 1.0), (control.minimum, control.maximum, control.step))

        fallback = AnalysisContext({"limits": "5,-5"}).limits(
            "limits", default=(-10, 10), minimum=-20, maximum=20
        )
        self.assertEqual((-10.0, 10.0), fallback)

    def test_inline_parameter_group_places_typed_controls_in_layout(self):
        def analyze(data, ui: AnalysisContext):
            with ui.tab("Parameterized"):
                with ui.parameter_group("Display parameters", columns=2):
                    threshold = ui.number("threshold", label="Threshold (dB)", default=2.5, step=0.1)
                    mode = ui.select("mode", label="Estimator", default="Mean", options=("Mean", "Maximum"))
                ui.plot(go.Figure(go.Scatter(y=[threshold], name=mode)), key="result")

        workspace = make_workspace(
            identifier="inline-parameters",
            name="Inline parameters",
            description="Layout controls",
            source=ExampleSource(),
            configure=no_parameters,
            process=identity_process,
            present=analyze,
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

        workspace = make_workspace(
            identifier="mixed-switcher",
            name="Mixed switcher",
            description="Mixed switched layouts",
            source=ExampleSource(),
            configure=no_parameters,
            process=identity_process,
            present=analyze,
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

        workspace = make_workspace(
            identifier="switchers",
            name="Switchers",
            description="Multiple local view selectors",
            source=ExampleSource(),
            configure=no_parameters,
            process=identity_process,
            present=analyze,
        )
        page = workspace.open_item("recording").page
        self.assertEqual(4, len(page.views))
        self.assertEqual(["buttons", "dropdown"], [node.props["selector"] for node in page.layout.children])

    def test_view_switcher_can_combine_multiple_selection_dimensions(self):
        def analyze(data, ui: AnalysisContext):
            with ui.tab("Waterfall"):
                ui.view_switcher(
                    ("Domain", "Channels"),
                    {
                        ("Time", "All"): go.Figure(),
                        ("Time", "Ch1"): go.Figure(),
                        ("Frequency", "All"): go.Figure(),
                        ("Frequency", "Ch1"): go.Figure(),
                    },
                    key="waterfall",
                    selector=("buttons", "dropdown"),
                )

        workspace = make_workspace(
            identifier="multi-dimensional-switcher",
            name="Multi-dimensional switcher",
            description="Independent local selectors choose one view",
            source=ExampleSource(),
            configure=no_parameters,
            process=identity_process,
            present=analyze,
        )
        page = workspace.open_item("recording").page
        switcher = page.layout.children[0]
        self.assertEqual(("Domain", "Channels"), switcher.props["labels"])
        self.assertEqual(("buttons", "dropdown"), switcher.props["selectors"])
        self.assertEqual((("Time", "Frequency"), ("All", "Ch1")), switcher.props["options"])
        self.assertEqual(((0, 0), (0, 1), (1, 0), (1, 1)), switcher.props["coordinates"])
        self.assertEqual(("waterfall:0", "waterfall:1"), switcher.props["selection_keys"])
        self.assertEqual(4, len(page.views))

    def test_plot_axis_navigation_is_an_explicit_page_contract(self):
        def analyze(data, ui: AnalysisContext):
            with ui.tab("Spectrum"):
                ui.plot(go.Figure(), key="spectrum", axis_navigation="bounded")
                ui.view_switcher(
                    "Channel",
                    {"One": go.Figure(), "Two": go.Figure()},
                    key="channel",
                    axis_navigation="bounded",
                )

        workspace = make_workspace(
            identifier="bounded-plots",
            name="Bounded plots",
            description="Explicit plot navigation",
            source=ExampleSource(),
            configure=no_parameters,
            process=identity_process,
            present=analyze,
        )
        page = workspace.open_item("recording").page
        self.assertEqual(["bounded", "bounded", "bounded"], [view.axis_navigation for view in page.views])

        ui = AnalysisContext({})
        with self.assertRaisesRegex(ValueError, "axis_navigation"):
            with ui.tab("Invalid"):
                ui.plot(go.Figure(), axis_navigation="locked")

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

        workspace = make_workspace(
            identifier="lifecycles",
            name="Lifecycles",
            description="Static and dynamic views",
            source=ExampleSource(),
            configure=no_parameters,
            process=identity_process,
            present=analyze,
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
