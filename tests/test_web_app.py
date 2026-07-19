from io import BytesIO
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from sigvue.plugin import AnalysisWorkspace, DirectorySource
from sigvue.web.application import (
    SigvueApp,
    WorkspaceModuleRegistration,
    _make_handler,
    _module_watch_snapshot,
)
from tests.fixtures import create_test_app, identity_process


class WebAppTests(unittest.TestCase):
    def create_example_app(self):
        return create_test_app()

    def test_app_lists_registered_workspaces(self):
        app = self.create_example_app()
        workspaces = app.list_workspaces()
        self.assertEqual({"test-workspace", "matplotlib-workspace"}, {workspace["id"] for workspace in workspaces})

    def test_workspace_defines_sortable_discovery_columns(self):
        listing = self.create_example_app().browse_items("test-workspace", {})
        self.assertEqual(
            ["date", "sample_rate", "rf_frequency"],
            [column["key"] for column in listing["columns"]],
        )
        self.assertEqual(2_000_000.0, listing["items"][0]["summary_fields"]["sample_rate"])
        self.assertIsNone(listing["items"][0]["summary_fields"]["rf_frequency"])

    def test_open_item_returns_layout_and_views(self):
        app = self.create_example_app()
        payload = app.open_item("test-workspace", "recording")
        self.assertEqual("recording", payload["item"]["id"])
        self.assertNotIn("status", payload["item"])
        self.assertNotIn("status", payload["page"])
        self.assertIn("summary", payload["page"]["views"])
        self.assertEqual("markdown", payload["page"]["rendered_views"][0]["kind"])
        self.assertTrue(payload["page"]["annotation"]["enabled"])
        self.assertTrue(payload["page"]["export"]["enabled"])
        self.assertNotIn("Analysis runtime", payload["page"]["statistics"])
        self.assertRegex(payload["page"]["runtime_statistics"]["Analysis runtime"], r"^\d+\.\d ms$")
        self.assertRegex(payload["page"]["runtime_statistics"]["View callbacks"], r"^\d+\.\d ms$")

    def test_workspace_controls_change_rendered_playback(self):
        app = self.create_example_app()
        payload = app.open_item(
            "test-workspace",
            "recording",
            {"gain": "2", "__playback_time_seconds": "0.75"},
        )
        page = payload["page"]
        self.assertEqual(1, len(page["controls"]))
        playback = next(view for view in page["rendered_views"] if view["name"] == "signal")["value"]
        self.assertEqual([2.0, 4.0, 6.0, 8.0], playback["data"][0]["y"])

    def test_recursive_directory_source_is_browsed_as_folders(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "root.dat").write_text("root", encoding="utf-8")
            (root / "campaign-a").mkdir()
            (root / "campaign-a" / "capture.dat").write_text("capture", encoding="utf-8")
            (root / "campaign-a" / "day-2").mkdir()
            (root / "campaign-a" / "day-2" / "capture.dat").write_text("nested", encoding="utf-8")

            def analyze(data, ui):
                with ui.tab("Data"):
                    ui.text(data, key="data")

            workspace = AnalysisWorkspace(
                identifier="nested-files",
                name="Nested files",
                description="Nested directory fixture",
                source=DirectorySource(root, pattern="*.dat", loader=lambda path: path.read_text(), recursive=True),
                process=identity_process,
                present=analyze,
            )
            app = SigvueApp()
            app.register_workspace(workspace)

            root_listing = app.browse_items("nested-files", {})
            self.assertEqual([{"name": "campaign-a", "path": ["campaign-a"]}], root_listing["directories"])
            self.assertEqual(["root.dat"], [item["id"] for item in root_listing["items"]])

            campaign = app.browse_items("nested-files", {"directory": ["campaign-a"]})
            self.assertEqual([{"name": "day-2", "path": ["campaign-a", "day-2"]}], campaign["directories"])
            self.assertEqual(["campaign-a::capture.dat"], [item["id"] for item in campaign["items"]])
            opened = app.open_item("nested-files", "campaign-a::capture.dat")
            self.assertEqual(["campaign-a"], opened["item"]["navigation_path"])

    def test_plugin_annotation_is_created_and_rediscovered(self):
        app = self.create_example_app()
        result = app.write_item_annotation(
            "test-workspace",
            "recording",
            {"__playback_time_seconds": "1.25", "__view_selection_channel": "2"},
            1.25,
            0.25,
            {"comment": "Check this interval"},
        )
        self.assertIsNone(result["label"])
        self.assertEqual("Check this interval", result["comment"])
        self.assertEqual(1.25, result["position_seconds"])
        self.assertEqual(
            {"channel": 2},
            app.registry.get("test-workspace").annotator.last_request.view_selections,
        )
        reopened = app.open_item("test-workspace", "recording")
        self.assertEqual([result], reopened["page"]["annotation"]["entries"])

        buffer_refresh = app.open_item(
            "test-workspace", "recording", {"__include_static_views": "false"}
        )
        self.assertIsNone(buffer_refresh["page"]["annotation"]["entries"])

    def test_launch_url_serves_browser_interface(self):
        app = self.create_example_app()
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/"
        handler._write_html = Mock()
        handler.do_GET()
        body = handler._write_html.call_args.args[0]
        self.assertIn("Sigvue", body)
        self.assertIn("Explore scientific and analytical results", body)
        self.assertIn('id="fullscreen-toggle"', body)
        self.assertIn('id="header-details"', body)
        self.assertIn('id="header-download"', body)
        self.assertIn('id="header-annotate"', body)
        self.assertNotIn('id="header-camera"', body)
        self.assertNotIn('id="header-log"', body)
        self.assertIn("p.annotation?.enabled", body)
        self.assertIn("p.export?.enabled", body)
        self.assertIn('id="export-scope"', body)
        self.assertIn('id="export-format"', body)
        self.assertIn("data-annotation-field", body)
        self.assertIn("annotationBoundPlot", body)
        self.assertIn("__view_selection_", body)
        self.assertIn("populatePlotBoundAnnotationFields", body)
        self.assertIn("binding.selection_policy==='box_preferred'", body)
        self.assertIn("plotSelectionRange", body)
        self.assertIn("plotly_selected", body)
        self.assertIn("plotly_deselect", body)
        self.assertIn("apiPost(`/workspaces/", body)
        self.assertIn('<span class="annotation-marker', body)
        self.assertIn("annotationMarkerGroups", body)
        self.assertIn("annotationMarkerColor", body)
        self.assertIn("--annotation-marker-color", body)
        self.assertIn("timeline_color_control", body)
        self.assertIn("data-sort", body)
        self.assertIn("discoveryValue", body)
        self.assertIn("summary_fields", body)
        self.assertIn("position>duration", body)
        self.assertIn("data-annotation-count", body)
        self.assertIn("target.dataset.annotationSignature===signature", body)
        self.assertIn("if(Array.isArray(p.annotation?.entries))", body)
        self.assertNotIn("marker.onclick=()=>activeAnnotationSeek", body)
        self.assertIn('class="toggle-control"', body)
        self.assertIn("c.type==='checkbox'?String(c.checked):c.value", body)
        self.assertIn("annotations.push(result)", body)
        self.assertIn("const form=event.currentTarget", body)
        self.assertIn("form.reset()", body)
        self.assertNotIn("event.currentTarget.reset()", body)
        self.assertIn("requestFullscreen()", body)
        self.assertIn("fullscreenchange", body)
        self.assertIn("catalog()", body)
        self.assertIn('id="workspace-search"', body)
        self.assertIn('placeholder="Search workspaces…"', body)
        self.assertIn("No matching workspaces.", body)
        self.assertIn("${w.name} ${w.description||''} ${w.category||''}", body)
        self.assertNotIn('id="reload-config"', body)
        self.assertNotIn("Reload browser.toml", body)
        self.assertNotIn('id="refresh"', body)
        self.assertNotIn("Refresh list", body)
        self.assertIn("data-folder", body)
        self.assertIn("data-directory-level", body)
        self.assertIn("parts[2]==='browse'", body)
        self.assertIn("params.append('directory',segment)", body)
        self.assertNotIn("applyTheme();boot()", body)
        self.assertIn("if(activeThemeRefresh)await activeThemeRefresh();else applyTheme()", body)
        self.assertIn("activeThemeRefresh=async()=>", body)
        self.assertIn("commitTheme(render)", body)
        self.assertIn("refresh(true,true)", body)
        self.assertIn("document.startViewTransition(update)", body)
        self.assertIn("preloadMatplotlibViews(p.rendered_views)", body)
        self.assertNotIn("refreshTheme(){applyTheme();", body)
        self.assertIn("if(isWindowed&&applied)startFrameworkWindowed", body)
        self.assertIn('type="color"', body)
        self.assertIn("'<strong>$1</strong>'", body)
        self.assertIn("control.group||'Analysis settings'", body)
        self.assertIn('class="style-picker"', body)
        self.assertIn('class="colormap-picker"', body)
        self.assertIn("function bindColormapPickers()", body)
        self.assertIn("control.option_previews", body)
        self.assertIn('class="limits-picker"', body)
        self.assertIn("function bindLimitsPickers()", body)
        self.assertIn('data-limit-number="lower"', body)
        self.assertIn('data-limit-number="upper"', body)
        self.assertNotIn('data-limit-range=', body)
        self.assertNotIn('data-limits-handle=', body)
        self.assertIn("data-style-swatch", body)
        self.assertIn("control.picker", body)
        self.assertIn('/assets/plotly.min.js', body)
        self.assertIn("updatePlotlyViews(p.rendered_views)", body)
        self.assertIn("updateMatplotlibViews(p.rendered_views)", body)
        self.assertIn("updateGenericViews(p.rendered_views)", body)
        self.assertIn('data-matplotlib-view', body)
        self.assertIn("node.kind==='control_slot'", body)
        self.assertIn('class="parameter-group"', body)
        self.assertIn('class="data-stage"', body)
        self.assertIn('class="workspace-sidebar"', body)
        self.assertIn('data-sidebar-toggle', body)
        self.assertIn("bindSidebar()", body)
        self.assertIn('class="view-stats"', body)
        self.assertIn('<h2>View details</h2>', body)
        self.assertIn('<h2>Runtime</h2>', body)
        self.assertIn('id="runtime-stats"', body)
        self.assertIn('data-client-stat="plotly-runtime"', body)
        self.assertIn("updateStatistics(p.statistics,p.runtime_statistics)", body)
        self.assertIn('class="layout-tab-pane', body)
        self.assertIn("bindLayoutTabs()", body)
        self.assertIn("__include_static_views:includeStatic", body)
        self.assertIn('class="data-table"', body)
        self.assertIn("gridTemplate(node.props.columns)", body)
        self.assertIn("function sizeDataStage()", body)
        self.assertIn('id="current-time"', body)
        self.assertIn('step="any"', body)
        self.assertIn("const seek=async(value,displayValue=false)=>", body)
        self.assertIn("const timelineUnits=", body)
        self.assertIn("function resolvedTimelineUnit(config)", body)
        self.assertIn("canonicalTime(value,config)", body)
        self.assertIn("formatTimelineTime", body)
        self.assertIn('id="jump-live"', body)
        self.assertIn("const isPlayback=['seek','live'].includes(p.playback.mode)", body)
        self.assertIn("isWindowed=p.playback.mode==='windowed'", body)
        self.assertIn("playbackConfig.mode==='live'", body)
        self.assertIn("__playback_follow_live:playbackFollowLive", body)
        self.assertIn("Object.assign(playbackConfig,result.page.playback)", body)
        self.assertIn("function startFrameworkWindowed(config,refresh)", body)
        self.assertIn("function startFrameworkSegmented(config,refresh)", body)
        self.assertIn("isSegmented=p.playback.mode==='segmented'", body)
        self.assertIn('id="segmented-track"', body)
        self.assertIn('id="segment-previous"', body)
        self.assertIn('id="segment-next"', body)
        self.assertIn("__segment_id:segmentId", body)
        self.assertIn('id="windowed-track"', body)
        self.assertIn('id="windowed-left"', body)
        self.assertIn('id="windowed-right"', body)
        self.assertIn('id="windowed-start"', body)
        self.assertIn('id="windowed-end"', body)
        self.assertIn('id="windowed-total"', body)
        self.assertIn('id="windowed-width"', body)
        self.assertIn('class="windowed-track-stack ${', body)
        self.assertIn('class="windowed-separator" aria-hidden="true">•</span>', body)
        self.assertNotIn('s buffer)</label>', body)
        self.assertIn("const editEndpoint=(kind,value)=>", body)
        self.assertIn("const editWidth=value=>", body)
        self.assertIn("__window_start_seconds:windowStart", body)
        self.assertIn("__window_end_seconds:windowEnd", body)
        self.assertIn("config.overview_values", body)
        self.assertIn("config.overview_series", body)
        self.assertIn("config.overview_durations_seconds", body)
        self.assertIn("config.overview_switcher_key", body)
        self.assertIn("redrawWindowOverview?.()", body)
        self.assertIn("rememberPlotResetRanges", body)
        self.assertIn("plotly_relayouting", body)
        self.assertIn("constrainPlotDuringPan", body)
        self.assertIn("view?.axis_navigation==='bounded'", body)
        self.assertIn("changedPlotResetRanges", body)
        self.assertIn("resetPlotAxes(plot)", body)
        self.assertIn("return false", body)
        self.assertIn("modeBarButtonsToAdd:['select2d']", body)
        self.assertIn("modeBarButtonsToRemove:['lasso2d']", body)
        self.assertIn("const scheduleCommit=()", body)
        self.assertIn("render();scheduleCommit()", body)
        self.assertIn("track.onpointerup=event=>", body)
        self.assertIn("finalCommit()", body)
        self.assertIn("window.innerHeight-stage.getBoundingClientRect().top", body)
        self.assertIn("--grid-rows:repeat(", body)
        self.assertNotIn("calc((100vh - 188px)/2)", body)
        self.assertIn("pane.classList.toggle('active'", body)
        self.assertNotIn("All statuses", body)
        self.assertNotIn('class="status"', body)

    def test_plotly_javascript_is_served(self):
        app = self.create_example_app()
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/assets/plotly.min.js"
        handler._write_javascript = Mock()
        handler.do_GET()
        javascript = handler._write_javascript.call_args.args[0]
        self.assertIn("plotly.js", javascript)

    def test_item_routes_decode_url_encoded_identifiers(self):
        app = Mock()
        app.open_item.return_value = {"item": {"id": "2mhz::lfm-2mhz"}}
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/workspaces/radar-waterfall/items/2mhz%3A%3Alfm-2mhz"
        handler._write_json = Mock()

        handler.do_GET()

        app.open_item.assert_called_once_with("radar-waterfall", "2mhz::lfm-2mhz", {})
        handler._write_json.assert_called_once_with(200, {"item": {"id": "2mhz::lfm-2mhz"}})

    def test_annotation_endpoint_routes_plugin_values(self):
        app = Mock()
        app.write_item_annotation.return_value = {"id": "a1", "position_seconds": 1.25}
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        payload = json.dumps(
            {
                "control_values": {"gain": "2", "__playback_time_seconds": "1.25"},
                "position_seconds": 1.25,
                "duration_seconds": 0.25,
                "values": {"comment": "Check this"},
            }
        ).encode("utf-8")
        handler.path = "/workspaces/test/items/capture.dat/annotations"
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = BytesIO(payload)
        handler._write_json = Mock()
        handler.do_POST()

        app.write_item_annotation.assert_called_once_with(
            "test",
            "capture.dat",
            {"gain": "2", "__playback_time_seconds": "1.25"},
            1.25,
            0.25,
            {"comment": "Check this"},
        )
        handler._write_json.assert_called_once_with(201, {"id": "a1", "position_seconds": 1.25})

    def test_plugin_export_runs_as_a_background_job(self):
        app = self.create_example_app()
        job_id = app.start_export(
            "test-workspace",
            "recording",
            {"__playback_time_seconds": "0.5"},
            "buffer",
            "json",
        )
        app._export_jobs[job_id].future.result(timeout=10)
        status = app.export_status(job_id)
        self.assertEqual("ready", status["status"])
        self.assertEqual("json", status["format"])
        self.assertEqual("recording-buffer.json", status["files"][0]["name"])
        path = app.export_file(job_id, status["files"][0]["name"])
        self.assertEqual("buffer", json.loads(path.read_text())["scope"])

    def test_export_endpoint_routes_plugin_scope_and_format(self):
        app = Mock()
        app.start_export.return_value = "job-1"
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        payload = json.dumps({
            "control_values": {"gain": "2"},
            "scope": "full",
            "format": "mat",
        }).encode("utf-8")
        handler.path = "/workspaces/test/items/capture.dat/exports"
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = BytesIO(payload)
        handler._write_json = Mock()
        handler.do_POST()

        app.start_export.assert_called_once_with("test", "capture.dat", {"gain": "2"}, "full", "mat")
        handler._write_json.assert_called_once_with(
            202,
            {"id": "job-1", "status": "pending", "status_url": "/exports/job-1"},
        )

    def test_workspace_without_exporter_rejects_export(self):
        app = self.create_example_app()
        job_id = app.start_export(
            "matplotlib-workspace",
            "recording",
            {"__playback_time_seconds": "0.5"},
            "buffer",
            "json",
        )
        with self.assertRaisesRegex(ValueError, "does not provide export support"):
            app._export_jobs[job_id].future.result(timeout=10)
        status = app.export_status(job_id)
        self.assertEqual("error", status["status"])

    def test_module_reload_watcher_includes_source_and_sigmf_data(self):
        project_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as directory:
            recording = Path(directory) / "capture.sigmf-data"
            recording.write_bytes(b"samples")
            watched = _module_watch_snapshot({project_root / "src", Path(directory)})
            self.assertIn(project_root / "src/sigvue/web/application.py", watched)
            self.assertIn(recording, watched)

    def test_browser_refresh_reloads_workspace_module_without_restarting_app(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            module_name = "sigvue_hot_reload_test"
            module_path = root / f"{module_name}.py"

            def write_workspace(name: str) -> None:
                module_path.write_text(
                    "from sigvue.core.models import WorkspaceMetadata\n"
                    "class Workspace:\n"
                    f"    metadata = WorkspaceMetadata('hot', '{name}', 'Reload test', '0.1.0')\n",
                    encoding="utf-8",
                )

            write_workspace("Before")
            sys.path.insert(0, directory)
            try:
                app = SigvueApp(
                    reload_workspaces=True,
                    workspace_modules=(WorkspaceModuleRegistration(module_name, "Workspace", root),),
                )
                app_identity = id(app)
                self.assertEqual("Before", app.list_workspaces()[0]["name"])

                previous_mtime = module_path.stat().st_mtime_ns
                write_workspace("After")
                os.utime(module_path, ns=(previous_mtime + 1_000_000, previous_mtime + 1_000_000))

                handler_type = _make_handler(app)
                handler = handler_type.__new__(handler_type)
                handler.path = "/workspaces"
                handler._write_json = Mock()
                handler.do_GET()
                status, payload = handler._write_json.call_args.args

                self.assertEqual(200, status)
                self.assertEqual("After", payload["workspaces"][0]["name"])
                self.assertEqual(app_identity, id(app))
            finally:
                sys.path.remove(directory)
                sys.modules.pop(module_name, None)

    def test_page_reload_reloads_browser_profile(self):
        app = Mock()
        app.title = "Reloaded title"
        app.subtitle = "Reloaded subtitle"
        app.config_path = Path("browser.toml")
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/"
        handler._write_html = Mock()

        handler.do_GET()

        app.reload_browser_profile.assert_called_once_with()
        body = handler._write_html.call_args.args[0]
        self.assertIn("Reloaded title", body)
        self.assertIn("Reloaded subtitle", body)

    def test_workspace_page_reload_reloads_browser_profile(self):
        app = Mock()
        app.title = "Sigvue"
        app.subtitle = "Workspace list"
        app.config_path = Path("browser.toml")
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/workspace/test-workspace"
        handler._write_html = Mock()

        handler.do_GET()

        app.reload_browser_profile.assert_called_once_with()
        handler._write_html.assert_called_once()


if __name__ == "__main__":
    unittest.main()
