import base64
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from plotly.graph_objects import Figure

from examples.generic import GenericExampleWorkspace
from examples.sigmf import _read_recording
from workspace_browser.web.application import (
    WorkspaceBrowserApp,
    WorkspaceModuleRegistration,
    _make_handler,
    _export_stem,
    _module_watch_snapshot,
    create_app,
)


class WebAppTests(unittest.TestCase):
    def create_example_app(self):
        return create_app(config_path=Path(__file__).resolve().parents[1] / "browser.example.toml")

    def test_create_app_has_example_workspace(self):
        app = self.create_example_app()
        workspaces = app.list_workspaces()
        self.assertEqual(6, len(workspaces))
        self.assertEqual(
            {"generic-example", "sigmf-viewer", "sigmf-matplotlib-viewer", "lfm-collection", "lfm-full-recording", "pri-waterfall"},
            {workspace["id"] for workspace in workspaces},
        )

    def test_open_item_returns_layout_and_views(self):
        app = self.create_example_app()
        payload = app.open_item("generic-example", "item-1")
        self.assertEqual("item-1", payload["item"]["id"])
        self.assertNotIn("status", payload["item"])
        self.assertNotIn("status", payload["page"])
        self.assertIn("summary", payload["page"]["views"])
        self.assertEqual("markdown", payload["page"]["rendered_views"][0]["kind"])

    def test_discovery_payload_does_not_assign_item_status(self):
        app = self.create_example_app()
        items = app.list_items("sigmf-viewer", {})
        self.assertTrue(all("status" not in item for item in items))

    def test_sigmf_example_has_time_and_frequency_tabs(self):
        app = self.create_example_app()
        items = app.list_items("generic-example", {})
        self.assertIn("sigmf-tone-demo", {item["id"] for item in items})
        payload = app.open_item("generic-example", "sigmf-tone-demo")
        self.assertEqual("tabs", payload["page"]["layout"]["kind"])
        self.assertEqual(8, len(payload["page"]["views"]))
        self.assertEqual(["Time Domain", "Frequency Domain"], [child["props"]["label"] for child in payload["page"]["layout"]["children"]])
        self.assertTrue(all(view["kind"] == "plotly" for view in payload["page"]["rendered_views"]))
        time_playback = payload["page"]["rendered_views"][0]["value"]
        self.assertIn("data", time_playback)
        self.assertIn("layout", time_playback)
        self.assertEqual("seek", payload["page"]["playback"]["mode"])

        opened = GenericExampleWorkspace().open_item("sigmf-tone-demo")
        self.assertIsInstance(opened.page.views[0].callback({}), Figure)

    def test_workspace_controls_change_rendered_playback(self):
        app = self.create_example_app()
        payload = app.open_item(
            "generic-example",
            "sigmf-tone-demo",
            {"buffer_size": "256", "amplitude_scale": "0.5", "spectrum_window": "rectangular", "__playback_time_seconds": "0.7"},
        )
        page = payload["page"]
        self.assertEqual(3, len(page["controls"]))
        playback = page["rendered_views"][0]["value"]
        self.assertEqual(256, len(playback["data"][0]["x"]))

    def test_file_backed_sigmf_viewer_reads_recording(self):
        app = self.create_example_app()
        items = app.list_items("sigmf-viewer", {})
        self.assertEqual(
            ["four-channel", "single-channel-bursts", "two-channel-sweep"],
            [item["id"] for item in items],
        )
        expected_view_counts = {"four-channel": 11, "single-channel-bursts": 5, "two-channel-sweep": 7}
        expected_playback_ends = {"four-channel": 1.875, "single-channel-bursts": 1.84, "two-channel-sweep": 1.9375}
        for item in items:
            self.assertTrue(item["source_reference"].endswith(f"{item['id']}.sigmf-meta"))
            page = app.open_item("sigmf-viewer", item["id"], {"__playback_time_seconds": "0.5"})["page"]
            self.assertEqual("seek", page["playback"]["mode"])
            self.assertEqual(expected_playback_ends[item["id"]], page["playback"]["duration_seconds"])
            self.assertEqual(expected_view_counts[item["id"]], len(page["rendered_views"]))
            self.assertEqual(["markdown", "table", "plotly"], [view["kind"] for view in page["rendered_views"][:3]])
            self.assertEqual("static", page["rendered_views"][0]["update"])

        single = app.open_item("sigmf-viewer", "single-channel-bursts")["page"]
        self.assertEqual(
            ["Calibration", "Time Domain", "Frequency Domain"],
            [child["props"]["label"] for child in single["layout"]["children"]],
        )
        calibration_layout = single["layout"]["children"][0]
        self.assertEqual((1, 2), calibration_layout["props"]["columns"])
        self.assertEqual(["column", "column"], [child["kind"] for child in calibration_layout["children"]])
        self.assertIn("Measured offset", single["rendered_views"][1]["value"][0])

        dynamic = app.open_item(
            "sigmf-viewer",
            "single-channel-bursts",
            {"__playback_time_seconds": "0.5", "__include_static_views": "false"},
        )["page"]
        self.assertEqual(["time-0", "frequency-0"], [view["name"] for view in dynamic["rendered_views"]])
        self.assertTrue(all(view["update"] == "dynamic" for view in dynamic["rendered_views"]))

    def test_matplotlib_sigmf_viewer_uses_native_figures(self):
        app = self.create_example_app()
        items = app.list_items("sigmf-matplotlib-viewer", {})
        self.assertEqual(["four-channel", "single-channel-bursts", "two-channel-sweep"], [item["id"] for item in items])
        page = app.open_item("sigmf-matplotlib-viewer", "four-channel", {"__playback_time_seconds": "0.5"})["page"]
        self.assertEqual("seek", page["playback"]["mode"])
        self.assertEqual(["Time Domain", "Frequency Domain"], [child["props"]["label"] for child in page["layout"]["children"]])
        self.assertEqual(8, len(page["rendered_views"]))
        self.assertTrue(all(view["kind"] == "matplotlib" for view in page["rendered_views"]))
        self.assertTrue(all(base64.b64decode(view["value"]).startswith(b"\x89PNG") for view in page["rendered_views"]))
        self.assertEqual("Matplotlib (PNG)", page["statistics"]["Renderer"])

    def test_pri_workspace_produces_waterfalls_and_max_holds(self):
        app = self.create_example_app()
        self.assertEqual(3, len(app.list_items("pri-waterfall", {})))
        page = app.open_item(
            "pri-waterfall",
            "single-channel-bursts",
            {"buffer_seconds": "1.5", "pri_seconds": "0.1", "seek_seconds": "0.03", "refresh_seconds": "0.2", "__playback_time_seconds": "0"},
        )["page"]
        self.assertEqual(5, len(page["controls"]))
        self.assertEqual(["float", "float", "float", "float", "select"], [control["control_type"] for control in page["controls"]])
        self.assertEqual([1.5, 0.1], [control["default"] for control in page["controls"][:2]])
        self.assertEqual(0.03, page["playback"]["step_seconds"])
        self.assertEqual(0.2, page["playback"]["refresh_interval_seconds"])
        self.assertEqual(0.5, page["playback"]["duration_seconds"])
        self.assertEqual(1, len(page["rendered_views"]))
        self.assertEqual(["scatter", "scatter", "heatmap", "heatmap"], [trace["type"] for trace in page["rendered_views"][0]["value"]["data"]])
        layout = page["rendered_views"][0]["value"]["layout"]
        top_height = layout["yaxis"]["domain"][1] - layout["yaxis"]["domain"][0]
        bottom_height = layout["yaxis3"]["domain"][1] - layout["yaxis3"]["domain"][0]
        self.assertAlmostEqual(2.0, bottom_height / top_height)
        self.assertEqual("x3", layout["xaxis"]["matches"])
        self.assertEqual("x4", layout["xaxis2"]["matches"])
        self.assertEqual("y3", layout["yaxis4"]["matches"])
        self.assertEqual(15, page["metadata"]["pri_count"])
        self.assertEqual("view_switcher", page["layout"]["children"][0]["kind"])
        self.assertEqual("1.5 s · 1200 samples", page["statistics"]["Buffer"])
        self.assertEqual("0.1 s · 80 samples", page["statistics"]["PRI"])
        self.assertEqual(15, page["statistics"]["Intervals per buffer"])
        self.assertRegex(page["statistics"]["Analysis runtime"], r"^\d+\.\d ms$")
        self.assertRegex(page["statistics"]["View callbacks"], r"^\d+\.\d ms$")

        multichannel = app.open_item("pri-waterfall", "four-channel")["page"]
        self.assertEqual(4, len(multichannel["rendered_views"]))
        self.assertEqual("dropdown", multichannel["layout"]["children"][0]["props"]["selector"])
        self.assertEqual(["Channel 1", "Channel 2", "Channel 3", "Channel 4"], [child["props"]["label"] for child in multichannel["layout"]["children"][0]["children"]])

    def test_sigmf_frame_reads_only_the_requested_pri_window(self):
        metadata_path = Path(__file__).resolve().parents[1] / "examples/data/single-channel-bursts.sigmf-meta"
        recording = _read_recording(metadata_path)
        data_path = recording.data_path
        requested_reads: list[int] = []

        class CountingStream:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def seek(self, offset):
                return self.stream.seek(offset)

            def read(self, size=-1):
                requested_reads.append(size)
                return self.stream.read(size)

        class CountingPath:
            def open(self, *args, **kwargs):
                return CountingStream(data_path.open(*args, **kwargs))

        recording = recording.__class__(
            recording.metadata_path,
            CountingPath(),
            recording.sample_rate,
            recording.datatype,
            recording.channel_count,
            recording.frame_count,
            recording.metadata,
        )
        offset, channels = recording.frame(0.5, 240)
        self.assertEqual(400, offset)
        self.assertEqual(240, len(channels[0]))
        self.assertEqual([240 * recording.bytes_per_frame], requested_reads)

    def test_launch_url_serves_browser_interface(self):
        app = self.create_example_app()
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/"
        handler._write_html = Mock()
        handler.do_GET()
        body = handler._write_html.call_args.args[0]
        self.assertIn("Signal Analysis Browser", body)
        self.assertIn('id="fullscreen-toggle"', body)
        self.assertIn('id="header-details"', body)
        self.assertIn('id="header-download"', body)
        self.assertIn('id="header-camera"', body)
        self.assertIn("/exports?", body)
        self.assertIn("runExport('mat'", body)
        self.assertIn("runExport('png'", body)
        self.assertIn("requestFullscreen()", body)
        self.assertIn("fullscreenchange", body)
        self.assertIn("catalog()", body)
        self.assertNotIn("applyTheme();boot()", body)
        self.assertIn("if(activeThemeRefresh)await activeThemeRefresh()", body)
        self.assertIn("activeThemeRefresh=()=>refresh(true)", body)
        self.assertIn('type="color"', body)
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
        self.assertIn("p.playback.mode!=='static'", body)
        self.assertIn("playbackConfig.mode==='live'", body)
        self.assertIn("__playback_follow_live:playbackFollowLive", body)
        self.assertIn("Object.assign(playbackConfig,result.page.playback)", body)
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

    def test_mat_export_runs_as_a_background_job(self):
        app = self.create_example_app()
        job_id = app.start_export(
            "sigmf-viewer",
            "single-channel-bursts",
            {"__playback_time_seconds": "0.5"},
            "mat",
        )
        app._export_jobs[job_id].future.result(timeout=10)
        status = app.export_status(job_id)
        self.assertEqual("ready", status["status"])
        self.assertEqual("mat", status["format"])
        self.assertEqual("single-channel-bursts-t0.5s-seek-analysis.mat", status["files"][0]["name"])
        path = app.export_file(job_id, status["files"][0]["name"])
        self.assertEqual(b"MATL", path.read_bytes()[:4])

    def test_camera_export_runs_as_a_background_job(self):
        app = self.create_example_app()
        job_id = app.start_export(
            "sigmf-matplotlib-viewer",
            "single-channel-bursts",
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
        self.assertEqual("capture-full-static", _export_stem("capture", "static", {}, delivered))

    def test_module_reload_watcher_includes_source_and_sigmf_data(self):
        project_root = Path(__file__).resolve().parents[1]
        watched = _module_watch_snapshot({project_root / "src", project_root / "examples"})
        self.assertIn(project_root / "src/workspace_browser/web/application.py", watched)
        self.assertIn(project_root / "examples/data/four-channel.sigmf-data", watched)

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
