from io import BytesIO
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from workspace_browser.plugin import AnalysisWorkspace, DirectorySource
from workspace_browser.web.application import (
    WorkspaceBrowserApp,
    WorkspaceModuleRegistration,
    _make_handler,
    _export_stem,
    _module_watch_snapshot,
)
from tests.fixtures import create_test_app


class WebAppTests(unittest.TestCase):
    def create_example_app(self):
        return create_test_app()

    def test_app_lists_registered_workspaces(self):
        app = self.create_example_app()
        workspaces = app.list_workspaces()
        self.assertEqual({"test-workspace", "matplotlib-workspace"}, {workspace["id"] for workspace in workspaces})

    def test_open_item_returns_layout_and_views(self):
        app = self.create_example_app()
        payload = app.open_item("test-workspace", "recording")
        self.assertEqual("recording", payload["item"]["id"])
        self.assertNotIn("status", payload["item"])
        self.assertNotIn("status", payload["page"])
        self.assertIn("summary", payload["page"]["views"])
        self.assertEqual("markdown", payload["page"]["rendered_views"][0]["kind"])
        self.assertFalse(payload["page"]["logging"]["enabled"])

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

    def test_seekable_file_item_writes_progress_log_beside_data(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "capture.dat"
            source.write_text("samples", encoding="utf-8")

            def analyze(data, ui):
                ui.playback(mode="seek", duration=3.0, step=0.25)
                gain = ui.number("gain", default=1.0, step=0.1)
                with ui.tab("Signal"):
                    ui.text(f"Gain {gain}", key="signal")

            workspace = AnalysisWorkspace(
                identifier="log-test",
                name="Log test",
                description="Progress log test",
                source=DirectorySource(root, pattern="*.dat", loader=lambda path: path.read_text(encoding="utf-8")),
                analyze=analyze,
            )
            app = WorkspaceBrowserApp()
            app.register_workspace(workspace)
            opened = app.open_item("log-test", "capture.dat", {"gain": "2", "__playback_time_seconds": "1.25"})
            self.assertTrue(opened["page"]["logging"]["enabled"])

            result = app.write_item_log(
                "log-test",
                "capture.dat",
                {"gain": "2", "__playback_time_seconds": "1.25", "__playback_follow_live": "false"},
                {"active_tab": "Signal", "view_selections": {"domain": "Frequency"}},
            )
            target = root / "logs" / result["filename"]
            self.assertTrue(target.is_file())
            self.assertIn("-t1.25s-", target.name)
            note = target.read_text(encoding="utf-8")
            self.assertIn("Playback mode: seek", note)
            self.assertIn("Playback position: 1.25 s", note)
            self.assertIn("Active tab: Signal", note)
            self.assertIn('"domain": "Frequency"', note)
            self.assertIn('"gain": "2"', note)
            reopened = app.open_item("log-test", "capture.dat", {"__playback_time_seconds": "0"})
            self.assertEqual(
                [{"filename": result["filename"], "position_seconds": 1.25}],
                reopened["page"]["logging"]["entries"],
            )

    def test_launch_url_serves_browser_interface(self):
        app = self.create_example_app()
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/"
        handler._write_html = Mock()
        handler.do_GET()
        body = handler._write_html.call_args.args[0]
        self.assertIn("Signal Analysis Browser", body)
        self.assertIn("Explore scientific and analytical results", body)
        self.assertIn('id="fullscreen-toggle"', body)
        self.assertIn('id="header-details"', body)
        self.assertIn('id="header-download"', body)
        self.assertIn('id="header-camera"', body)
        self.assertIn('id="header-log"', body)
        self.assertLess(body.index('id="header-log"'), body.index('id="header-camera"'))
        self.assertIn('class="sidebar-toggle icon-button"', body)
        self.assertIn('<circle cx="12" cy="13" r="3.25"/>', body)
        self.assertNotIn("📷", body)
        self.assertIn("/exports?", body)
        self.assertIn("runExport('mat'", body)
        self.assertIn("runExport('png'", body)
        self.assertIn("p.logging?.enabled", body)
        self.assertIn("apiPost(`/workspaces/", body)
        self.assertIn('id="log-markers"', body)
        self.assertIn('class="log-marker"', body)
        self.assertIn("activePlaybackSeek?.(marker.dataset.logPosition)", body)
        self.assertIn("progressLogs.push(result)", body)
        self.assertIn("button.innerHTML=original", body)
        self.assertIn("requestFullscreen()", body)
        self.assertIn("fullscreenchange", body)
        self.assertIn("catalog()", body)
        self.assertNotIn("applyTheme();boot()", body)
        self.assertIn("if(activeThemeRefresh)await activeThemeRefresh()", body)
        self.assertIn("activeThemeRefresh=async()=>", body)
        self.assertIn("if(isWindowed&&applied)startFrameworkWindowed", body)
        self.assertIn('type="color"', body)
        self.assertIn("'<strong>$1</strong>'", body)
        self.assertIn("control.group||'Analysis settings'", body)
        self.assertIn('class="style-picker"', body)
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
        self.assertIn('data-client-stat="plotly-runtime"', body)
        self.assertIn("updateStatistics(p.statistics)", body)
        self.assertIn('class="layout-tab-pane', body)
        self.assertIn("bindLayoutTabs()", body)
        self.assertIn("__include_static_views:includeStatic", body)
        self.assertIn('class="data-table"', body)
        self.assertIn("gridTemplate(node.props.columns)", body)
        self.assertIn("function sizeDataStage()", body)
        self.assertIn('id="current-time"', body)
        self.assertIn('step="any"', body)
        self.assertIn("const seek=async value=>", body)
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
        self.assertIn("const editEndpoint=(kind,value)=>", body)
        self.assertIn("__window_start_seconds:windowStart", body)
        self.assertIn("__window_end_seconds:windowEnd", body)
        self.assertIn("config.overview_values", body)
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

    def test_progress_log_endpoint_routes_json_context(self):
        app = Mock()
        app.write_item_log.return_value = {"filename": "capture-t1.25s-note.txt"}
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        payload = json.dumps(
            {
                "control_values": {"gain": "2", "__playback_time_seconds": "1.25"},
                "active_tab": "Signal",
                "view_selections": {"domain": "Frequency"},
            }
        ).encode("utf-8")
        handler.path = "/workspaces/log-test/items/capture.dat/logs"
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = BytesIO(payload)
        handler._write_json = Mock()
        handler.do_POST()

        app.write_item_log.assert_called_once_with(
            "log-test",
            "capture.dat",
            {"gain": "2", "__playback_time_seconds": "1.25"},
            {"active_tab": "Signal", "view_selections": {"domain": "Frequency"}},
        )
        handler._write_json.assert_called_once_with(201, {"filename": "capture-t1.25s-note.txt"})

    def test_mat_export_runs_as_a_background_job(self):
        app = self.create_example_app()
        job_id = app.start_export(
            "test-workspace",
            "recording",
            {"__playback_time_seconds": "0.5"},
            "mat",
        )
        app._export_jobs[job_id].future.result(timeout=10)
        status = app.export_status(job_id)
        self.assertEqual("ready", status["status"])
        self.assertEqual("mat", status["format"])
        self.assertEqual("recording-t0.5s-seek-analysis.mat", status["files"][0]["name"])
        path = app.export_file(job_id, status["files"][0]["name"])
        self.assertEqual(b"MATL", path.read_bytes()[:4])

    def test_camera_export_runs_as_a_background_job(self):
        app = self.create_example_app()
        job_id = app.start_export(
            "matplotlib-workspace",
            "recording",
            {"__playback_time_seconds": "0.5"},
            "png",
        )
        result = app._export_jobs[job_id].future.result(timeout=20)
        status = app.export_status(job_id)
        self.assertEqual("ready", status["status"])
        self.assertEqual("png", status["format"])
        self.assertGreater(result["plot_count"], 0)
        path = app.export_file(job_id, status["files"][0]["name"])
        self.assertEqual(b"PK", path.read_bytes()[:2])

    def test_export_names_use_actual_delivered_window_time(self):
        delivered = {
            "sample_rate": 1_000.0,
            "start_sample": 125,
            "samples": SimpleNamespace(shape=(4, 20)),
        }
        self.assertEqual(
            "capture-t0.125s-buffer0.02s-live",
            _export_stem("capture", "live", {"__playback_time_seconds": 0.1}, delivered),
        )
        self.assertEqual(
            "capture-t0.25s-buffer0.5s-windowed",
            _export_stem(
                "capture",
                "windowed",
                {"__window_start_seconds": 0.25, "__window_end_seconds": 0.75},
                {},
            ),
        )
        self.assertEqual("capture-full-static", _export_stem("capture", "static", {}, delivered))

    def test_module_reload_watcher_includes_source_and_sigmf_data(self):
        project_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as directory:
            recording = Path(directory) / "capture.sigmf-data"
            recording.write_bytes(b"samples")
            watched = _module_watch_snapshot({project_root / "src", Path(directory)})
            self.assertIn(project_root / "src/workspace_browser/web/application.py", watched)
            self.assertIn(recording, watched)

    def test_browser_refresh_reloads_workspace_module_without_restarting_app(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            module_name = "workspace_browser_hot_reload_test"
            module_path = root / f"{module_name}.py"

            def write_workspace(name: str) -> None:
                module_path.write_text(
                    "from workspace_browser.core.models import WorkspaceMetadata\n"
                    "class Workspace:\n"
                    f"    metadata = WorkspaceMetadata('hot', '{name}', 'Reload test', '0.1.0')\n",
                    encoding="utf-8",
                )

            write_workspace("Before")
            sys.path.insert(0, directory)
            try:
                app = WorkspaceBrowserApp(
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


if __name__ == "__main__":
    unittest.main()
