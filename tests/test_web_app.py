from io import BytesIO
from argparse import Namespace
from concurrent.futures import CancelledError
from contextlib import redirect_stdout
from io import StringIO
import json
import os
import sys
import unittest
import numpy as np
import plotly.graph_objects as go
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock

from sigvue.core.workspace import (
    Analysis,
    DataResource,
    DirectorySource,
    Presentation,
    Source,
    Workspace,
)
from sigvue import (
    Batch,
    BatchDestination,
    BatchRequest,
    BatchResult,
    CapabilityChoice,
    Files,
    add_viewport_heatmap,
)
from sigvue.web.application import (
    SigvueApp,
    WorkspaceModuleRegistration,
    _make_handler,
    _module_watch_snapshot,
    _run_batch_command,
)
from tests.fixtures import (
    IdentityAnalysis,
    MemorySource,
    create_test_app,
    identity_process,
)


class WebAppTests(unittest.TestCase):
    def create_example_app(self):
        return create_test_app()

    def test_app_lists_registered_workspaces(self):
        app = self.create_example_app()
        workspaces = app.list_workspaces()
        self.assertEqual(
            {"test-workspace", "matplotlib-workspace"},
            {workspace["id"] for workspace in workspaces},
        )

    def test_workspace_defines_sortable_discovery_columns(self):
        listing = self.create_example_app().browse_items("test-workspace", {})
        self.assertEqual(
            ["date", "sample_rate", "rf_frequency"],
            [column["key"] for column in listing["columns"]],
        )
        self.assertEqual(
            2_000_000.0, listing["items"][0]["summary_fields"]["sample_rate"]
        )
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
        self.assertRegex(
            payload["page"]["runtime_statistics"]["Analysis runtime"], r"^\d+\.\d ms$"
        )
        self.assertRegex(
            payload["page"]["runtime_statistics"]["View callbacks"], r"^\d+\.\d ms$"
        )
        self.assertRegex(
            payload["page"]["runtime_statistics"]["Discovery runtime"], r"^\d+\.\d ms$"
        )
        self.assertRegex(
            payload["page"]["runtime_statistics"]["Source open runtime"],
            r"^\d+\.\d ms$",
        )
        self.assertRegex(
            payload["page"]["runtime_statistics"]["Workspace total"], r"^\d+\.\d ms$"
        )
        self.assertRegex(
            payload["page"]["runtime_statistics"]["Server total"], r"^\d+\.\d ms$"
        )

    def test_workspace_controls_change_rendered_playback(self):
        app = self.create_example_app()
        payload = app.open_item(
            "test-workspace",
            "recording",
            {"gain": "2", "__playback_time_seconds": "0.75"},
        )
        page = payload["page"]
        self.assertEqual(1, len(page["controls"]))
        playback = next(
            view for view in page["rendered_views"] if view["name"] == "signal"
        )["value"]
        self.assertEqual([2.0, 4.0, 6.0, 8.0], playback["data"][0]["y"])

    def test_lazy_workspace_renders_only_the_selected_tab_and_switcher_view(self):
        resolved = []

        class LazyPresentation(Presentation):
            def present(self, data, ui):
                def figure(name):
                    def build():
                        resolved.append(name)
                        return go.Figure(go.Scatter(y=[name]))

                    return build

                with ui.tab("Summary"):
                    ui.plot(figure("summary"), key="summary")
                with ui.tab("Details"):
                    ui.view_switcher(
                        "Channel",
                        {"One": figure("one"), "Two": figure("two")},
                        key="channel",
                    )

        workspace = Workspace._from_runtime_components(
            identifier="lazy-workspace",
            name="Lazy workspace",
            description="Active views only",
            source=MemorySource(),
            analysis=IdentityAnalysis(),
            presentation=LazyPresentation(),
            lazy_views=True,
        )
        app = SigvueApp()
        app.register_workspace(workspace)

        initial = app.open_item("lazy-workspace", "recording")
        selected = app.open_item(
            "lazy-workspace",
            "recording",
            {"__view_selection___tabs": "1", "__view_selection_channel": "1"},
        )

        self.assertTrue(initial["page"]["lazy_views"])
        self.assertEqual(
            ["summary"], [view["name"] for view in initial["page"]["rendered_views"]]
        )
        self.assertEqual(
            ["channel-1"], [view["name"] for view in selected["page"]["rendered_views"]]
        )
        self.assertEqual(["summary", "two"], resolved)

    def test_eager_workspace_remains_the_default(self):
        payload = self.create_example_app().open_item("test-workspace", "recording")
        self.assertFalse(payload["page"]["lazy_views"])
        self.assertEqual(
            set(payload["page"]["views"]),
            {view["name"] for view in payload["page"]["rendered_views"]},
        )

    def test_open_item_renders_only_requested_plot_viewport(self):
        class RasterPresentation(Presentation):
            def present(self, data, ui):
                figure = go.Figure()
                add_viewport_heatmap(
                    figure,
                    viewport=ui.plot_viewport("heatmap"),
                    x=np.arange(100),
                    y=np.arange(80),
                    z=np.arange(8_000, dtype=float).reshape(80, 100),
                    colorscale="Viridis",
                    render_width=5,
                    render_height=4,
                )
                figure.update_xaxes(range=[-0.5, 99.5])
                figure.update_yaxes(range=[-0.5, 79.5])
                with ui.tab("Heatmap"):
                    ui.plot(figure, key="heatmap", axis_navigation="bounded")

        workspace = Workspace._from_runtime_components(
            identifier="raster-workspace",
            name="Raster workspace",
            description="Raster callback fixture",
            source=MemorySource(),
            analysis=IdentityAnalysis(),
            presentation=RasterPresentation(),
        )
        app = SigvueApp()
        app.register_workspace(workspace)

        payload = app.open_item(
            "raster-workspace",
            "recording",
            {
                "__plot_viewports": json.dumps(
                    {"heatmap": {"xaxis": [40, 49], "yaxis": [20, 27]}}
                )
            },
        )

        rendered = payload["page"]["rendered_views"][0]
        image = rendered["value"]["layout"]["images"][0]
        self.assertTrue(rendered["rasterized"])
        self.assertEqual((39.5, 10.0), (image["x"], image["sizex"]))
        self.assertEqual((27.5, 8.0), (image["y"], image["sizey"]))

    def test_recursive_directory_source_is_browsed_as_folders(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "root.dat").write_text("root", encoding="utf-8")
            (root / "campaign-a").mkdir()
            (root / "campaign-a" / "capture.dat").write_text(
                "capture", encoding="utf-8"
            )
            (root / "campaign-a" / "day-2").mkdir()
            (root / "campaign-a" / "day-2" / "capture.dat").write_text(
                "nested", encoding="utf-8"
            )

            def analyze(data, ui):
                with ui.tab("Data"):
                    ui.text(data, key="data")

            class TextPresentation(Presentation):
                def present(self, data, ui):
                    analyze(data, ui)

            workspace = Workspace._from_runtime_components(
                identifier="nested-files",
                name="Nested files",
                description="Nested directory fixture",
                source=DirectorySource(
                    root,
                    pattern="*.dat",
                    loader=lambda path: path.read_text(),
                    recursive=True,
                ),
                analysis=IdentityAnalysis(),
                presentation=TextPresentation(),
            )
            app = SigvueApp()
            app.register_workspace(workspace)

            root_listing = app.browse_items("nested-files", {})
            self.assertEqual(
                [{"name": "campaign-a", "path": ["campaign-a"]}],
                root_listing["directories"],
            )
            self.assertEqual(
                ["root.dat"], [item["id"] for item in root_listing["items"]]
            )

            campaign = app.browse_items("nested-files", {"directory": ["campaign-a"]})
            self.assertEqual(
                [{"name": "day-2", "path": ["campaign-a", "day-2"]}],
                campaign["directories"],
            )
            self.assertEqual(
                ["campaign-a::capture.dat"], [item["id"] for item in campaign["items"]]
            )
            opened = app.open_item("nested-files", "campaign-a::capture.dat")
            self.assertEqual(["campaign-a"], opened["item"]["navigation_path"])

            flattened = Workspace(
                identifier="flat-files",
                name="Flat files",
                description="Flattened recursive fixture",
                reader=Files(
                    root,
                    "*.dat",
                    lambda path: path.read_text(),
                    recursive=True,
                ),
                view=analyze,
                flatten_discovery=True,
            )
            flat_app = SigvueApp()
            flat_app.register_workspace(flattened)
            flat_listing = flat_app.browse_items("flat-files", {})
            self.assertEqual([], flat_listing["directories"])
            self.assertEqual(
                [
                    "campaign-a::capture.dat",
                    "campaign-a::day-2::capture.dat",
                    "root.dat",
                ],
                [item["id"] for item in flat_listing["items"]],
            )
            flat_opened = flat_app.open_item(
                "flat-files",
                "campaign-a::day-2::capture.dat",
            )
            self.assertEqual([], flat_opened["item"]["navigation_path"])

    def test_workspace_annotation_is_created_and_rediscovered(self):
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
        self.assertIn('id="header-back"', body)
        self.assertIn('id="header-forward"', body)
        self.assertIn('id="header-refresh"', body)
        self.assertIn('id="fullscreen-toggle"', body)
        self.assertIn('id="workspace-add" type="button" hidden', body)
        self.assertIn("workspaceAdd.hidden=true;headerDetails.hidden=true", body)
        self.assertIn("workspaceAdd.hidden=false;", body)
        self.assertIn('id="workspace-wizard"', body)
        self.assertIn('id="workspace-factory"', body)
        self.assertIn('id="workspace-data-root"', body)
        self.assertIn('id="workspace-flatten"', body)
        self.assertIn('id="workspace-persist"', body)
        self.assertIn("`/workspace-setup${query}`", body)
        self.assertIn("apiPost('/workspaces',payload)", body)
        self.assertIn("No workspaces are open.", body)
        self.assertIn("window.pywebview?.api?.choose_directory", body)
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
        self.assertIn("headerBack.onclick=()=>history.back()", body)
        self.assertIn("headerForward.onclick=()=>history.forward()", body)
        self.assertIn("headerRefresh.onclick=async()=>", body)
        self.assertIn("await boot(true)", body)
        self.assertNotIn("location.reload()", body)
        self.assertIn("if(navigate)pushRoute", body)
        self.assertIn("catalog()", body)
        self.assertIn("singleWorkspaceMode=workspaces.length===1", body)
        self.assertIn(
            "if(singleWorkspaceMode){const workspace=workspaces[0];"
            "return items(workspace.id,workspace.name,false,[])}",
            body,
        )
        self.assertIn(
            "singleWorkspaceMode?'/':`/workspace/${encodeURIComponent(id)}`",
            body,
        )
        self.assertIn('id="workspace-search"', body)
        self.assertIn('placeholder="Search workspaces…"', body)
        self.assertIn("No matching workspaces.", body)
        self.assertIn("function batchMenuHtml", body)
        self.assertIn("function bindBatchMenus", body)
        self.assertIn("headerNotifications.open=false", body)
        self.assertIn("headerNotifications.onclick=event=>", body)
        self.assertIn("function followInternalResultLink(event)", body)
        self.assertIn(
            "!target.pathname.startsWith('/results/')",
            body,
        )
        self.assertIn(
            "pushRoute(`${target.pathname}${target.search}${target.hash}`);"
            "void boot()",
            body,
        )
        self.assertIn(
            "document.addEventListener('click',event=>{closeBatchMenus();"
            "headerNotifications.open=false;followInternalResultLink(event)});",
            body,
        )
        self.assertIn("closeBatchMenus(menu)", body)
        self.assertIn("data-batch-action", body)
        self.assertIn(
            "batchState(action)==='ready'?'↻ rerun':",
            body,
        )
        self.assertIn("Regenerate existing result", body)
        self.assertIn("function batchNotificationTitle", body)
        self.assertIn('id="header-notifications"', body)
        self.assertIn('id="notification-list"', body)
        self.assertIn("#notification-list { max-height:min(330px", body)
        self.assertIn("overflow-y:auto; overscroll-behavior:contain", body)
        self.assertIn("notifications.unshift", body)
        self.assertIn('id="notification-toasts"', body)
        self.assertIn("notificationToasts.append(toast)", body)
        self.assertIn("setTimeout(()=>toast.remove(),3000)", body)
        self.assertIn("function monitorBatchJob", body)
        self.assertIn("function batchProgressHtml", body)
        self.assertIn("data-cancel-batch", body)
        self.assertIn("notification-progress-success", body)
        self.assertIn("notification-progress-failed", body)
        self.assertIn("Copy folder", body)
        self.assertIn("Browse live results", body)
        self.assertIn("No finished results yet.", body)
        self.assertIn("resultBrowserTimer=setTimeout(refresh,750)", body)
        self.assertIn("generation!==resultBrowserGeneration", body)
        self.assertIn("selectedVersion=null", body)
        self.assertIn("This image is still being finalized.", body)
        self.assertIn(
            "header .home-title { all:unset; display:block; min-width:0;",
            body,
        )
        self.assertIn(
            "header #app-subtitle { min-width:0; flex:0 1 auto; "
            "overflow:hidden;",
            body,
        )
        self.assertIn(".batch-menu[open] { z-index:35 }", body)
        self.assertIn(
            "batchControlsHtml(listing.batch,workspaceBatchUrl)",
            body,
        )
        self.assertIn('class="item-action-rail"', body)
        self.assertIn("function bindItemActionRail", body)
        self.assertIn("data-batch-folder", body)
        self.assertIn("batchFolderIcon", body)
        self.assertIn('class="batch-folder-label">Browse</span>', body)
        self.assertIn("function syncBatchNotifications", body)
        self.assertIn("syncBatchNotifications();boot()", body)
        self.assertIn("data-batch-status", body)
        self.assertIn("notification-status.pending,.notification-status.running", body)
        self.assertIn("sessionStorage.getItem(key)", body)
        self.assertNotIn("renderNotifications();headerNotifications.open=true", body)
        self.assertIn("data-dismiss-notification", body)
        self.assertNotIn("data-batch-result", body)
        self.assertIn('<span class="batch-play" aria-hidden="true">▶</span>', body)
        self.assertNotIn('class="batch-indicator', body)
        self.assertIn("Copy path", body)
        self.assertIn('target="_blank"', body)
        self.assertIn('<th class="tags-column">Tags</th>', body)
        self.assertNotIn('<th class="batch-cell">Run</th>', body)
        self.assertNotIn('<td class="batch-cell">', body)
        self.assertIn(
            ".item-toolbar { display:grid; "
            "grid-template-columns:minmax(0,1fr) 132px;",
            body,
        )
        self.assertIn(
            ".item-browser-layout { display:grid; "
            "grid-template-columns:minmax(0,1fr) 132px;",
            body,
        )
        self.assertIn('class="batch-controls"', body)
        self.assertIn(".batch-menu.ready summary { color:#16803c;", body)
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
        self.assertIn(
            "if(activeThemeRefresh)await activeThemeRefresh();else applyTheme()", body
        )
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
        self.assertIn('class="settings-group"', body)
        self.assertIn('class="settings-group-body"', body)
        self.assertIn('class="style-picker"', body)
        self.assertIn('class="colormap-picker"', body)
        self.assertIn("function bindColormapPickers()", body)
        self.assertIn("control.option_previews", body)
        self.assertIn('class="limits-picker"', body)
        self.assertIn("function bindLimitsPickers(onCommit)", body)
        self.assertIn('data-limit-number="lower"', body)
        self.assertIn('data-limit-number="upper"', body)
        self.assertNotIn("data-limit-range=", body)
        self.assertNotIn("data-limits-handle=", body)
        self.assertIn("data-style-swatch", body)
        self.assertIn("control.picker", body)
        self.assertIn("/assets/plotly.min.js", body)
        self.assertIn("mountRenderedViews(p.rendered_views)", body)
        self.assertIn("updatePlotlyViews(updates)", body)
        self.assertIn(
            "layout=layoutWithPlotViewport(view,restored)",
            body,
        )
        self.assertIn(
            "Plotly.react(target,view.value.data||[],layout,plotlyConfig)"
            ".finally(()=>{target._sigvueUpdating=false})",
            body,
        )
        update_plotly = body.split("async function updatePlotlyViews", 1)[1].split(
            "async function updateMatplotlibViews", 1
        )[0]
        self.assertNotIn("Plotly.relayout", update_plotly)
        self.assertNotIn("Plotly.Plots.resize", update_plotly)
        self.assertIn("activeViewChanged=()=>p.lazy_views?refresh(true)", body)
        self.assertIn("data-view-slot", body)
        self.assertIn("updateMatplotlibViews(p.rendered_views)", body)
        self.assertIn("updateGenericViews(p.rendered_views)", body)
        self.assertIn("data-matplotlib-view", body)
        self.assertIn("node.kind==='control_slot'", body)
        self.assertIn(
            "['colormap','limits'].includes(control.control_type)"
            "?customControlHtml(control,values)",
            body,
        )
        self.assertIn('class="parameter-group"', body)
        self.assertIn('class="data-stage"', body)
        self.assertIn('class="workspace-sidebar"', body)
        self.assertIn("data-sidebar-toggle", body)
        self.assertIn("bindSidebar()", body)
        self.assertIn('class="view-stats"', body)
        self.assertIn("<h2>View details</h2>", body)
        self.assertIn("<h2>Runtime</h2>", body)
        self.assertIn('id="runtime-stats"', body)
        self.assertIn("Data & analysis", body)
        self.assertIn("View generation", body)
        self.assertIn("Browser rendering", body)
        self.assertIn('class="runtime-total"', body)
        self.assertIn('data-client-stat="total-runtime"', body)
        self.assertNotIn("Plotly render</dt>", body)
        self.assertIn('data-client-stat="browser-runtime"', body)
        self.assertIn("setClientRuntime('browser-runtime'", body)
        self.assertIn("updateStatistics(p.statistics,p.runtime_statistics)", body)
        self.assertIn('class="layout-tab-pane', body)
        self.assertIn("data-view-selection-keys", body)
        self.assertIn("data-view-coordinates", body)
        self.assertIn("dimensionLabels.map", body)
        self.assertIn("bindLayoutTabs(()=>activeViewChanged?.())", body)
        self.assertIn("__include_static_views:includeStatic", body)
        self.assertIn('class="data-table"', body)
        self.assertIn(
            ".table-wrap { width:100%; height:100%; min-height:0; "
            "max-height:100%; overflow:auto;",
            body,
        )
        self.assertIn(
            "[data-render-view] { width:100%; height:100%; min-width:0; "
            "min-height:0; overflow:auto; overscroll-behavior:contain; "
            "scrollbar-gutter:stable",
            body,
        )
        self.assertIn(".data-table th { position:sticky; top:0; z-index:1;", body)
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
        self.assertIn(
            "const isPlayback=['seek','live'].includes(p.playback.mode)", body
        )
        self.assertIn("app.innerHTML='<div class=\"empty\">Opening item…</div>'", body)
        self.assertLess(
            body.index("app.innerHTML='<div class=\"empty\">Opening item…</div>'"),
            body.index("app.className='item-page'"),
        )
        self.assertNotIn(
            "app.innerHTML='<div class=\"empty\">Discovering items…</div>'", body
        )
        self.assertNotIn("Loading workspaces…", body)
        self.assertIn("isWindowed=p.playback.mode==='windowed'", body)
        self.assertIn("playbackConfig.mode==='live'", body)
        self.assertIn("__playback_follow_live:playbackFollowLive", body)
        self.assertIn("Object.assign(playbackConfig,result.page.playback)", body)
        self.assertIn(
            "function startFrameworkWindowed(config,refresh,controls=[])",
            body,
        )
        self.assertIn("function startFrameworkSegmented(config,refresh)", body)
        self.assertIn("isSegmented=p.playback.mode==='segmented'", body)
        self.assertIn('id="segmented-track"', body)
        self.assertIn('id="segment-previous"', body)
        self.assertIn('id="segment-next"', body)
        self.assertIn('id="segment-toggle"', body)
        self.assertIn('id="segment-rate"', body)
        self.assertIn('id="segment-step"', body)
        self.assertIn('aria-label="Segment refresh rate"', body)
        self.assertIn('aria-label="Segments per refresh"', body)
        self.assertIn("segmentedBar.prepend(segmentActions)", body)
        self.assertIn(
            '<div class="segment-actions"><button id="segment-previous" type="button">Previous</button>'
            '<button id="segment-next" type="button">Next</button></div>',
            body,
        )
        self.assertIn("function fixedSegmentTime(seconds,config)", body)
        self.assertIn("value.toFixed(2)", body)
        self.assertIn("time.textContent=formatSegmentRange", body)
        self.assertIn("const bindHold=(button,direction)=>", body)
        self.assertIn('class="segment-marker-layer"', body)
        self.assertIn("reduce((bounds,value)=>[Math.min(", body)
        self.assertIn("const nearestSegment=clientX=>", body)
        self.assertIn("track.onpointerdown=event=>", body)
        self.assertIn("track.onpointermove=event=>", body)
        self.assertIn("queuedIdentifier=identifier", body)
        self.assertIn("setInterval(()=>void advance(direction),150)", body)
        self.assertIn("button.onclick=()=>", body)
        self.assertIn("const scheduleAuto=()=>", body)
        self.assertIn("1000/rate", body)
        self.assertIn(
            "(selectedIndex()+direction*playbackStep()+segments.length)"
            "%segments.length",
            body,
        )
        self.assertIn(
            "previous.disabled=next.disabled=toggle.disabled=disabled",
            body,
        )
        self.assertIn("lifecycle!==segmentedPlaybackGeneration", body)
        self.assertIn("__segment_id:segmentId", body)
        self.assertIn('id="windowed-track"', body)
        self.assertIn('id="windowed-left"', body)
        self.assertIn('id="windowed-right"', body)
        self.assertIn('id="windowed-start"', body)
        self.assertIn('id="windowed-end"', body)
        self.assertIn('id="windowed-total"', body)
        self.assertIn('id="windowed-width"', body)
        self.assertIn('id="windowed-full-extent"', body)
        self.assertIn('class="windowed-track-stack ${', body)
        self.assertIn(
            '<div class="windowed-track" id="windowed-track">${hasWindowOverview?`<span class="windowed-label"',
            body,
        )
        self.assertIn(".annotation-marker { position:absolute; top:0; bottom:0;", body)
        self.assertIn(".windowed-track-stack { flex:1; height:30px;", body)
        self.assertIn(
            ".windowed-track { position:relative; width:100%; height:30px;",
            body,
        )
        self.assertIn(".windowed-full-extent { position:absolute;", body)
        self.assertIn(
            ".windowed-width-label { display:flex; flex:none; min-width:0;", body
        )
        self.assertIn(".windowed-bar .windowed-width { width:118px }", body)
        self.assertNotIn(".windowed-track-stack.has-overview .windowed-track", body)
        self.assertNotIn("windowed-separator", body)
        self.assertNotIn("s buffer)</label>", body)
        self.assertIn("const editEndpoint=(kind,value)=>", body)
        self.assertIn("const editWidth=value=>", body)
        self.assertIn("__window_start_seconds:windowStart", body)
        self.assertIn("__window_end_seconds:windowEnd", body)
        self.assertIn("config.overview_values", body)
        self.assertIn("config.overview_heatmap", body)
        self.assertIn("config.overview_colormap_control", body)
        self.assertIn("config.overview_limits_control", body)
        self.assertIn("selectedHeatStops", body)
        self.assertIn("selectedHeatLimits", body)
        self.assertIn(
            "startFrameworkWindowed(p.playback,refresh,p.controls)",
            body,
        )
        self.assertIn("const prepareHeatmap=()=>", body)
        self.assertIn(
            "(rowCount-1-rowIndex)*columnCount+columnIndex",
            body,
        )
        self.assertIn(
            "windowStart=0;windowEnd=selectedDuration();render();finalCommit()",
            body,
        )
        self.assertIn("config.overview_series", body)
        self.assertIn("config.overview_durations_seconds", body)
        self.assertIn("config.overview_switcher_key", body)
        self.assertIn("redrawWindowOverview?.()", body)
        self.assertIn("rememberPlotResetRanges", body)
        self.assertIn("function managedPlotlyLayout(view)", body)
        self.assertIn("delete layout.uirevision", body)
        self.assertIn(
            "delete layout.width;delete layout.height;layout.autosize=true", body
        )
        self.assertIn("managedPlotlyLayout(view),plotlyConfig", body)
        self.assertIn("plotly_relayouting", body)
        self.assertIn("plotly_relayout", body)
        self.assertIn("__plot_viewports:JSON.stringify(plotViewportPayload())", body)
        self.assertIn("bindLimitsPickers(settingsChanged)", body)
        self.assertIn("if(onCommit)void onCommit()", body)
        self.assertIn("if(x.closest('[data-limits-picker]'))return", body)
        self.assertIn("view.rasterized?onViewportChanged:null", body)
        self.assertIn("if(plot._sigvueUpdating||plot._sigvueResetting)return", body)
        self.assertIn("target._sigvueUpdating=true", body)
        self.assertIn("target._sigvueUpdating=false", body)
        self.assertIn("constrainPlotDuringPan", body)
        self.assertIn("view?.axis_navigation==='bounded'", body)
        self.assertIn("function plotResetState(view)", body)
        self.assertIn("currentPlotViewport(target)", body)
        self.assertIn("function capturePlotViewport(plot,event)", body)
        self.assertIn("plot._sigvueViewport=viewport", body)
        self.assertIn("target._sigvueViewport=Object.fromEntries", body)
        self.assertIn(
            "restoredPlotViewport(view,viewport,previous,state.reset,state.bounds)",
            body,
        )
        self.assertIn("translatedAxisRange", body)
        self.assertIn("{range,base:plot._sigvueResetRanges", body)
        self.assertIn("function layoutWithPlotViewport(view,viewport)", body)
        self.assertNotIn("update={...changed,...restored,...images}", body)
        self.assertIn("requestsPlotReset(event)", body)
        self.assertIn("resetPlotAxes(plot,onViewportChanged)", body)
        self.assertIn("plot._sigvueResetting=true", body)
        self.assertIn("plot._sigvueResetting=false;onReset?.()", body)
        self.assertIn("return false", body)
        self.assertIn("modeBarButtonsToAdd:['select2d']", body)
        self.assertIn("modeBarButtonsToRemove:['lasso2d']", body)
        self.assertIn("doubleClick:'reset'", body)
        self.assertIn("const scheduleCommit=()", body)
        self.assertIn("render();scheduleCommit()", body)
        self.assertIn("track.onpointerup=event=>", body)
        self.assertIn("finalCommit()", body)
        self.assertIn("window.innerHeight-stage.getBoundingClientRect().top", body)
        self.assertIn("--grid-rows:repeat(", body)
        self.assertNotIn("calc((100vh - 188px)/2)", body)
        self.assertIn("pane.classList.toggle('active'", body)
        self.assertIn("body.hold-item-layout main", body)
        self.assertIn("document.body.classList.add('hold-item-layout')", body)
        self.assertIn("new MutationObserver", body)
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

    def test_workspace_setup_endpoint_reports_session_defaults(self):
        app = self.create_example_app()
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/workspace-setup"
        handler._write_json = Mock()

        handler.do_GET()

        status, payload = handler._write_json.call_args.args
        self.assertEqual(200, status)
        self.assertIn("factories", payload)
        self.assertEqual(
            {"test-workspace", "matplotlib-workspace"},
            {workspace["id"] for workspace in payload["workspaces"]},
        )
        self.assertEqual(
            str((Path.cwd() / "browser.toml").resolve()),
            payload["default_profile_path"],
        )

    def test_workspace_setup_endpoint_adds_a_session_workspace(self):
        app = SigvueApp()
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        payload = json.dumps(
            {
                "use": "tests.fixtures:create_workspace",
                "id": "wizard-data",
                "name": "Wizard data",
                "description": "Added through the setup endpoint",
                "category": "test",
                "tags": ["wizard"],
                "flatten_discovery": True,
                "config": {},
                "persist": False,
                "profile_path": None,
            }
        ).encode("utf-8")
        handler.path = "/workspaces"
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = BytesIO(payload)
        handler._write_json = Mock()

        handler.do_POST()

        status, result = handler._write_json.call_args.args
        self.assertEqual(201, status)
        self.assertEqual("wizard-data", result["workspace"]["id"])
        self.assertIsNone(result["persisted_to"])
        self.assertEqual(
            ["wizard-data"],
            [workspace["id"] for workspace in app.list_workspaces()],
        )
        self.assertTrue(app.registry.get("wizard-data").flatten_discovery)

    def test_item_routes_decode_url_encoded_identifiers(self):
        app = Mock()
        app.open_item.return_value = {"item": {"id": "2mhz::lfm-2mhz"}}
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/workspaces/radar-waterfall/items/2mhz%3A%3Alfm-2mhz"
        handler._write_json = Mock()

        handler.do_GET()

        app.open_item.assert_called_once_with("radar-waterfall", "2mhz::lfm-2mhz", {})
        handler._write_json.assert_called_once_with(
            200, {"item": {"id": "2mhz::lfm-2mhz"}}
        )

    def test_annotation_endpoint_routes_capability_values(self):
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
        handler._write_json.assert_called_once_with(
            201, {"id": "a1", "position_seconds": 1.25}
        )

    def test_workspace_export_runs_as_a_background_job(self):
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

    def test_workspace_batch_runs_item_and_workspace_actions_in_background(self):
        class ExampleBatch(Batch):
            item_actions = (CapabilityChoice("summarize", "Summarize item"),)
            workspace_actions = (CapabilityChoice("compile", "Compile workspace"),)

            def run_item(self, resource, source_data, request: BatchRequest, directory):
                target = directory / "item.txt"
                target.write_text(
                    f"{resource.identifier}:{sum(source_data)}", encoding="utf-8"
                )
                report = directory / "report.html"
                report.write_text("<h1>Item report</h1>", encoding="utf-8")
                tile = directory / "report.assets" / "0" / "0_0.png"
                tile.parent.mkdir(parents=True)
                tile.write_bytes(b"tile")
                return BatchResult(
                    (target, report),
                    "Item summarized",
                    (tile,),
                )

            def run_workspace(
                self, resources, open_resource, request: BatchRequest, directory
            ):
                target = directory / "workspace.txt"
                target.write_text(
                    str(
                        sum(
                            request.each(
                                resources,
                                lambda resource: sum(open_resource(resource)),
                            )
                        )
                    ),
                    encoding="utf-8",
                )
                return BatchResult((target,), "Workspace compiled")

        base = self.create_example_app().registry.get("test-workspace")
        workspace = Workspace._from_runtime_components(
            identifier="batch-workspace",
            name="Batch workspace",
            description="Background jobs",
            source=base._legacy_runtime.source,
            delivery=base._legacy_runtime.delivery,
            analysis=base._legacy_runtime.analysis,
            presentation=base._legacy_runtime.presentation,
            batch=ExampleBatch(),
        )
        app = SigvueApp()
        app.register_workspace(workspace)

        listing = app.browse_items("batch-workspace", {})
        self.assertEqual(
            "summarize", listing["items"][0]["batch"]["actions"][0]["value"]
        )
        self.assertEqual("compile", listing["batch"]["actions"][0]["value"])

        item_job = app.start_batch("batch-workspace", "summarize", "recording")
        app._batch_jobs[item_job].future.result(timeout=10)
        item_status = app.batch_status(item_job)
        self.assertEqual("ready", item_status["status"])
        self.assertEqual("batch-workspace", item_status["workspace_id"])
        self.assertEqual("Batch workspace", item_status["workspace_name"])
        self.assertEqual("recording", item_status["item_id"])
        self.assertEqual("Summarize item", item_status["action_label"])
        self.assertEqual(f"/batches/{item_job}", item_status["status_url"])
        self.assertEqual("Item summarized", item_status["summary"])
        self.assertTrue(Path(item_status["files"][0]["path"]).is_absolute())
        self.assertIsNone(item_status["files"][0]["open_url"])
        self.assertEqual(
            f"/batches/{item_job}/report.html", item_status["files"][1]["open_url"]
        )
        self.assertEqual(
            "recording:10.0", app.batch_file(item_job, "item.txt").read_text()
        )
        self.assertEqual(
            b"tile",
            app.batch_file(
                item_job,
                "report.assets/0/0_0.png",
            ).read_bytes(),
        )
        self.assertEqual(
            ("report.assets/0/0_0.png",),
            app.batch_assets(item_job),
        )
        with self.assertRaises(KeyError):
            app.batch_file(item_job, "../report.assets/0/0_0.png")
        refreshed = app.browse_items("batch-workspace", {})
        item_action = refreshed["items"][0]["batch"]["actions"][0]
        self.assertEqual("ready", item_action["status"])
        self.assertEqual("item.txt", item_action["files"][0]["name"])

        workspace_job = app.start_batch("batch-workspace", "compile")
        app._batch_jobs[workspace_job].future.result(timeout=10)
        self.assertEqual(
            "Workspace compiled", app.batch_status(workspace_job)["summary"]
        )
        self.assertEqual(
            {
                "completed": 1,
                "succeeded": 1,
                "failed": 0,
                "total": 1,
            },
            {
                key: app.batch_status(workspace_job)["progress"][key]
                for key in ("completed", "succeeded", "failed", "total")
            },
        )
        recent_jobs = app.batch_statuses()["jobs"]
        self.assertEqual([workspace_job, item_job], [job["id"] for job in recent_jobs])
        workspace_action = app.list_workspaces()[0]["batch"]["actions"][0]
        self.assertEqual("ready", workspace_action["status"])

        app.batch_file(item_job, "item.txt").unlink()
        missing_status = app.batch_status(item_job)
        self.assertEqual("error", missing_status["status"])
        self.assertIn("item.txt", missing_status["detail"])

        with TemporaryDirectory() as output:
            stream = StringIO()
            with redirect_stdout(stream):
                result = _run_batch_command(
                    app,
                    Namespace(
                        list_batch=False,
                        workspace="batch-workspace",
                        item="recording",
                        action="summarize",
                        output=Path(output),
                        json=False,
                    ),
                )
            self.assertEqual(0, result)
            self.assertEqual("recording:10.0", (Path(output) / "item.txt").read_text())
            self.assertEqual(
                b"tile",
                (
                    Path(output)
                    / "report.assets"
                    / "0"
                    / "0_0.png"
                ).read_bytes(),
            )
            self.assertIn("saved:", stream.getvalue())

    def test_batch_directory_results_are_browseable_searchable_and_copyable(self):
        class DirectoryBatch(Batch):
            item_actions = (CapabilityChoice("render", "Render gallery"),)

            def run_item(
                self,
                resource,
                source_data,
                request: BatchRequest,
                directory,
            ):
                gallery = directory / "gallery"
                (gallery / "frames").mkdir(parents=True)
                (gallery / "frames" / "storm 01.png").write_bytes(b"png")
                (gallery / "notes.txt").write_text(
                    "frame notes",
                    encoding="utf-8",
                )
                image = directory / "preview.png"
                image.write_bytes(b"png")
                archive = directory / "frames.zip"
                archive.write_bytes(b"zip")
                return BatchResult(
                    (gallery, image, archive),
                    "Gallery rendered",
                )

        base = self.create_example_app().registry.get("test-workspace")
        workspace = Workspace._from_runtime_components(
            identifier="directory-batch-workspace",
            name="Directory batch workspace",
            description="Directory results",
            source=base._legacy_runtime.source,
            delivery=base._legacy_runtime.delivery,
            analysis=base._legacy_runtime.analysis,
            presentation=base._legacy_runtime.presentation,
            batch=DirectoryBatch(),
        )
        app = SigvueApp()
        app.register_workspace(workspace)

        job_id = app.start_batch(
            "directory-batch-workspace",
            "render",
            "recording",
        )
        app._batch_jobs[job_id].future.result(timeout=10)
        status = app.batch_status(job_id)
        self.assertEqual("ready", status["status"])
        self.assertEqual(
            f"/results/job/{job_id}",
            status["result_browser_url"],
        )
        artifacts = {artifact["name"]: artifact for artifact in status["files"]}
        self.assertEqual("directory", artifacts["gallery"]["kind"])
        self.assertEqual(
            f"/results/job/{job_id}/gallery",
            artifacts["gallery"]["browse_url"],
        )
        self.assertIsNone(artifacts["gallery"]["open_url"])
        self.assertEqual(
            f"/batches/{job_id}/preview.png",
            artifacts["preview.png"]["open_url"],
        )
        self.assertIsNone(artifacts["frames.zip"]["open_url"])
        self.assertEqual(
            f"/batches/{job_id}/frames.zip?download=1",
            artifacts["frames.zip"]["download_url"],
        )
        nested = app.batch_file(job_id, "gallery/frames/storm 01.png")
        self.assertEqual(b"png", nested.read_bytes())
        with self.assertRaises(KeyError):
            app.batch_file(job_id, "gallery/../../preview.png")

        listing = app.batch_directory(
            job_id,
            "gallery",
            "frames",
        )
        self.assertEqual(
            ["storm 01.png"],
            [entry["name"] for entry in listing["entries"]],
        )
        self.assertEqual("image", listing["entries"][0]["kind"])
        self.assertEqual(
            f"/batches/{job_id}/gallery/frames/storm%2001.png",
            listing["entries"][0]["url"],
        )
        with self.assertRaises(KeyError):
            app.batch_directory(job_id, "gallery", "../../")
        output_listing = app.batch_outputs(job_id)
        self.assertEqual(
            {"gallery", "preview.png", "frames.zip"},
            {entry["name"] for entry in output_listing["entries"]},
        )
        self.assertEqual(
            ["storm 01.png"],
            [
                entry["name"]
                for entry in app.batch_outputs(
                    job_id,
                    "gallery/frames",
                )["entries"]
            ],
        )
        with self.assertRaises(KeyError):
            app.batch_outputs(job_id, "../")

        with TemporaryDirectory() as output:
            result = _run_batch_command(
                app,
                Namespace(
                    list_batch=False,
                    workspace="directory-batch-workspace",
                    item="recording",
                    action="render",
                    output=Path(output),
                    json=True,
                ),
            )
            self.assertEqual(0, result)
            self.assertEqual(
                b"png",
                (
                    Path(output)
                    / "gallery"
                    / "frames"
                    / "storm 01.png"
                ).read_bytes(),
            )

    def test_workspace_batch_reports_progress_and_continues_after_item_error(self):
        second_started = Event()
        release_second = Event()

        class MultipleSource(Source):
            def discover(self):
                return [
                    DataResource(
                        f"item-{index}",
                        f"Item {index}",
                        source=(float(index),),
                    )
                    for index in range(3)
                ]

            def open(self, resource):
                return resource.source

        class ProgressBatch(Batch):
            workspace_actions = (CapabilityChoice("render", "Render workspace"),)

            def __init__(self, output):
                self.output = output

            def workspace_destination(self, resources, request):
                return BatchDestination(
                    self.output,
                    tuple(f"{resource.identifier}.txt" for resource in resources),
                    "Workspace outputs are ready",
                )

            def run_workspace(
                self,
                resources,
                open_resource,
                request: BatchRequest,
                directory,
            ):
                def render(resource):
                    if resource.identifier == "item-1":
                        second_started.set()
                        if not release_second.wait(timeout=10):
                            raise TimeoutError("Test batch was not released")
                        raise RuntimeError("bad radar file")
                    target = directory / f"{resource.identifier}.txt"
                    target.write_text(
                        str(sum(open_resource(resource))),
                        encoding="utf-8",
                    )
                    return target

                outputs = request.each(resources, render)
                return BatchResult(outputs, f"Rendered {len(outputs)} outputs")

        base = self.create_example_app().registry.get("test-workspace")
        with TemporaryDirectory() as output:
            (Path(output) / "item-2.txt").write_text(
                "stale result from an earlier run",
                encoding="utf-8",
            )
            workspace = Workspace._from_runtime_components(
                identifier="progress-workspace",
                name="Progress workspace",
                description="Progress fixture",
                source=MultipleSource(),
                delivery=base._legacy_runtime.delivery,
                analysis=base._legacy_runtime.analysis,
                presentation=base._legacy_runtime.presentation,
                batch=ProgressBatch(Path(output)),
            )
            app = SigvueApp()
            app.register_workspace(workspace)

            job_id = app.start_batch("progress-workspace", "render")
            self.assertTrue(second_started.wait(timeout=2))
            running = app.batch_status(job_id)
            self.assertEqual("running", running["status"])
            self.assertEqual(
                f"/results/job/{job_id}",
                running["result_browser_url"],
            )
            self.assertEqual(1, running["progress"]["completed"])
            self.assertEqual(1, running["progress"]["succeeded"])
            self.assertEqual(
                ["ready", "running", "pending"],
                [item["status"] for item in running["progress"]["items"]],
            )
            self.assertEqual(
                ["item-0.txt"],
                [file["name"] for file in running["files"]],
            )
            live_listing = app.batch_outputs(job_id)
            self.assertEqual("running", live_listing["status"])
            self.assertFalse(live_listing["complete"])
            self.assertEqual(
                ["item-0.txt"],
                [entry["name"] for entry in live_listing["entries"]],
            )
            self.assertEqual(
                "0.0",
                app.batch_file(job_id, "item-0.txt").read_text(
                    encoding="utf-8"
                ),
            )
            with self.assertRaises(KeyError):
                app.batch_file(job_id, "item-2.txt")

            release_second.set()
            app._batch_jobs[job_id].future.result(timeout=10)
            completed = app.batch_status(job_id)
            self.assertEqual("ready", completed["status"])
            self.assertEqual(3, completed["progress"]["completed"])
            self.assertEqual(2, completed["progress"]["succeeded"])
            self.assertEqual(1, completed["progress"]["failed"])
            self.assertEqual(
                ["ready", "error", "ready"],
                [item["status"] for item in completed["progress"]["items"]],
            )
            self.assertIn(
                "RuntimeError: bad radar file",
                completed["progress"]["items"][1]["log"],
            )
            self.assertIn("1 of 3 items failed", completed["summary"])
            self.assertEqual(
                {"item-0.txt", "item-2.txt"},
                {file["name"] for file in completed["files"]},
            )
            final_listing = app.batch_outputs(job_id)
            self.assertEqual("ready", final_listing["status"])
            self.assertTrue(final_listing["complete"])
            self.assertEqual(
                {"item-0.txt", "item-2.txt"},
                {entry["name"] for entry in final_listing["entries"]},
            )
            self.assertEqual(str(Path(output).resolve()), completed["output_directory"])

    def test_running_item_batch_can_be_cancelled_and_returns_to_idle(self):
        started = Event()

        class CancellableBatch(Batch):
            item_actions = (CapabilityChoice("wait", "Wait"),)

            def run_item(self, resource, source_data, request, directory):
                started.set()
                while not request.cancelled:
                    request.raise_if_cancelled()
                    Event().wait(0.01)
                request.raise_if_cancelled()

        base = self.create_example_app().registry.get("test-workspace")
        workspace = Workspace._from_runtime_components(
            identifier="cancel-workspace",
            name="Cancel workspace",
            description="Cancellation fixture",
            source=base._legacy_runtime.source,
            delivery=base._legacy_runtime.delivery,
            analysis=base._legacy_runtime.analysis,
            presentation=base._legacy_runtime.presentation,
            batch=CancellableBatch(),
        )
        app = SigvueApp()
        app.register_workspace(workspace)

        job_id = app.start_batch(
            "cancel-workspace",
            "wait",
            "recording",
        )
        self.assertTrue(started.wait(timeout=2))
        cancelling = app.cancel_batch(job_id)
        self.assertIn(cancelling["status"], {"cancelling", "cancelled"})
        with self.assertRaises(CancelledError):
            app._batch_jobs[job_id].future.result(timeout=10)
        self.assertEqual("cancelled", app.batch_status(job_id)["status"])
        action = app.browse_items("cancel-workspace", {})["items"][0]["batch"][
            "actions"
        ][0]
        self.assertEqual("idle", action["status"])

    def test_running_batch_is_available_to_page_independent_notifications(self):
        started = Event()
        release = Event()

        class SlowBatch(Batch):
            item_actions = (CapabilityChoice("wait", "Wait for result"),)

            def run_item(self, resource, source_data, request, directory):
                started.set()
                if not release.wait(timeout=10):
                    raise TimeoutError("Test batch was not released")
                target = directory / "result.txt"
                target.write_text("done", encoding="utf-8")
                return BatchResult((target,), "Slow result complete")

        base = self.create_example_app().registry.get("test-workspace")
        workspace = Workspace._from_runtime_components(
            identifier="slow-workspace",
            name="Slow workspace",
            description="Notification fixture",
            source=base._legacy_runtime.source,
            delivery=base._legacy_runtime.delivery,
            analysis=base._legacy_runtime.analysis,
            presentation=base._legacy_runtime.presentation,
            batch=SlowBatch(),
        )
        app = SigvueApp()
        app.register_workspace(workspace)

        job_id = app.start_batch("slow-workspace", "wait", "recording")
        self.assertTrue(started.wait(timeout=2))
        running = app.batch_statuses()["jobs"]
        self.assertEqual(1, len(running))
        self.assertEqual(job_id, running[0]["id"])
        self.assertEqual("running", running[0]["status"])
        self.assertEqual("Slow workspace", running[0]["workspace_name"])
        self.assertEqual("recording", running[0]["item_title"])
        self.assertEqual("Wait for result", running[0]["action_label"])

        release.set()
        app._batch_jobs[job_id].future.result(timeout=10)
        self.assertEqual("ready", app.batch_statuses()["jobs"][0]["status"])

    def test_durable_batch_destination_is_ready_in_a_fresh_app(self):
        with TemporaryDirectory() as output:
            output_path = Path(output)
            runs = []

            class DurableBatch(Batch):
                item_actions = (CapabilityChoice("report", "Build report"),)

                def item_destination(self, resource, request):
                    return BatchDestination(
                        output_path,
                        (f"{resource.identifier}.html",),
                        "Report already exists",
                    )

                def run_item(self, resource, source_data, request, directory):
                    runs.append(resource.identifier)
                    target = directory / f"{resource.identifier}.html"
                    target.write_text(
                        f"<h1>Report {len(runs)}</h1>",
                        encoding="utf-8",
                    )
                    tile = (
                        directory
                        / f"{resource.identifier}.assets"
                        / "4"
                        / "2_0.png"
                    )
                    tile.parent.mkdir(parents=True, exist_ok=True)
                    tile.write_bytes(b"tile")
                    return BatchResult(
                        (target,),
                        "Report generated",
                        (tile,),
                    )

            base = self.create_example_app().registry.get("test-workspace")

            def make_app():
                workspace = Workspace._from_runtime_components(
                    identifier="durable-workspace",
                    name="Durable workspace",
                    description="Persistent reports",
                    source=base._legacy_runtime.source,
                    delivery=base._legacy_runtime.delivery,
                    analysis=base._legacy_runtime.analysis,
                    presentation=base._legacy_runtime.presentation,
                    batch=DurableBatch(),
                )
                result = SigvueApp()
                result.register_workspace(workspace)
                return result

            first = make_app()
            job_id = first.start_batch("durable-workspace", "report", "recording")
            first._batch_jobs[job_id].future.result(timeout=10)
            expected = output_path / "recording.html"
            self.assertTrue(expected.is_file())
            self.assertEqual("<h1>Report 1</h1>", expected.read_text())

            relaunched = make_app()
            action = relaunched.browse_items("durable-workspace", {})["items"][0][
                "batch"
            ]["actions"][0]
            self.assertEqual("ready", action["status"])
            self.assertEqual(str(expected.resolve()), action["files"][0]["path"])
            _, _, collection_scope, collection_token = action[
                "result_browser_url"
            ].split("/")
            self.assertEqual("saved", collection_scope)
            collection = relaunched.declared_batch_outputs(collection_token)
            self.assertEqual("ready", collection["status"])
            self.assertTrue(collection["complete"])
            self.assertEqual(
                ["recording.html"],
                [entry["name"] for entry in collection["entries"]],
            )
            self.assertEqual(
                expected.resolve(),
                relaunched.declared_batch_file(
                    collection_token,
                    "recording.html",
                ),
            )
            self.assertTrue(action["files"][0]["open_url"].startswith("/batch-files/"))
            _, _, _, collection_token = action["result_browser_url"].split("/")
            collection = relaunched.declared_batch_outputs(collection_token)
            self.assertTrue(collection["complete"])
            self.assertEqual(
                ["recording.html"],
                [entry["name"] for entry in collection["entries"]],
            )
            _, _, token, filename = action["files"][0]["open_url"].split("/")
            self.assertEqual(
                expected.resolve(), relaunched.declared_batch_file(token, filename)
            )
            tile = output_path / "recording.assets" / "4" / "2_0.png"
            self.assertEqual(
                tile.resolve(),
                relaunched.declared_batch_file(
                    token,
                    "recording.assets/4/2_0.png",
                ),
            )
            with self.assertRaises(KeyError):
                relaunched.declared_batch_file(
                    token,
                    "../recording.assets/4/2_0.png",
                )

            rerun_id = relaunched.start_batch(
                "durable-workspace",
                "report",
                "recording",
            )
            relaunched._batch_jobs[rerun_id].future.result(timeout=10)
            self.assertEqual("<h1>Report 2</h1>", expected.read_text())
            self.assertEqual(
                "ready",
                relaunched.batch_status(rerun_id)["status"],
            )
            encoded = relaunched._batch_files(None, output_path, ("report #1.html",))[0]
            self.assertIn("report%20%231.html", encoded["open_url"])

    def test_durable_batch_directory_is_browseable_after_relaunch(self):
        with TemporaryDirectory() as output:
            output_path = Path(output)

            class DurableDirectoryBatch(Batch):
                item_actions = (CapabilityChoice("gallery", "Build gallery"),)

                def item_destination(self, resource, request):
                    return BatchDestination(
                        output_path,
                        (f"{resource.identifier}-gallery",),
                        "Gallery already exists",
                    )

                def run_item(
                    self,
                    resource,
                    source_data,
                    request,
                    directory,
                ):
                    gallery = directory / f"{resource.identifier}-gallery"
                    gallery.mkdir(parents=True, exist_ok=True)
                    (gallery / "frame.png").write_bytes(b"frame")
                    return BatchResult((gallery,), "Gallery generated")

            base = self.create_example_app().registry.get("test-workspace")

            def make_app():
                workspace = Workspace._from_runtime_components(
                    identifier="durable-directory-workspace",
                    name="Durable directory workspace",
                    description="Persistent directory results",
                    source=base._legacy_runtime.source,
                    delivery=base._legacy_runtime.delivery,
                    analysis=base._legacy_runtime.analysis,
                    presentation=base._legacy_runtime.presentation,
                    batch=DurableDirectoryBatch(),
                )
                result = SigvueApp()
                result.register_workspace(workspace)
                return result

            first = make_app()
            job_id = first.start_batch(
                "durable-directory-workspace",
                "gallery",
                "recording",
            )
            first._batch_jobs[job_id].future.result(timeout=10)

            relaunched = make_app()
            action = relaunched.browse_items(
                "durable-directory-workspace",
                {},
            )["items"][0]["batch"]["actions"][0]
            self.assertEqual("ready", action["status"])
            artifact = action["files"][0]
            self.assertEqual("directory", artifact["kind"])
            _, _, _, collection_token = action["result_browser_url"].split("/")
            collection = relaunched.declared_batch_outputs(collection_token)
            self.assertEqual(
                ["recording-gallery"],
                [entry["name"] for entry in collection["entries"]],
            )
            collection_listing = relaunched.declared_batch_outputs(
                collection_token,
                "recording-gallery",
            )
            self.assertEqual(
                ["frame.png"],
                [entry["name"] for entry in collection_listing["entries"]],
            )
            _, _, _, token, filename = artifact["browse_url"].split("/")
            self.assertEqual(
                (output_path / "recording-gallery").resolve(),
                relaunched.declared_batch_file(token, filename),
            )
            self.assertEqual(
                b"frame",
                relaunched.declared_batch_file(
                    token,
                    "recording-gallery/frame.png",
                ).read_bytes(),
            )
            with self.assertRaises(KeyError):
                relaunched.declared_batch_file(
                    token,
                    "recording-gallery/../outside.png",
                )
            listing = relaunched.declared_batch_directory(
                token,
                "recording-gallery",
            )
            self.assertEqual("frame.png", listing["entries"][0]["name"])

    def test_export_endpoint_routes_capability_scope_and_format(self):
        app = Mock()
        app.start_export.return_value = "job-1"
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        payload = json.dumps(
            {
                "control_values": {"gain": "2"},
                "scope": "full",
                "format": "mat",
            }
        ).encode("utf-8")
        handler.path = "/workspaces/test/items/capture.dat/exports"
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = BytesIO(payload)
        handler._write_json = Mock()
        handler.do_POST()

        app.start_export.assert_called_once_with(
            "test", "capture.dat", {"gain": "2"}, "full", "mat"
        )
        handler._write_json.assert_called_once_with(
            202,
            {"id": "job-1", "status": "pending", "status_url": "/exports/job-1"},
        )

    def test_item_batch_endpoint_dispatches_without_opening_the_item_view(self):
        app = Mock()
        app.start_batch.return_value = "batch-1"
        started_status = {
            "id": "batch-1",
            "workspace_id": "test",
            "item_id": "capture.dat",
            "action": "report",
            "action_label": "Build report",
            "status": "running",
            "status_url": "/batches/batch-1",
        }
        app.batch_status.return_value = started_status
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        payload = json.dumps({"action": "report"}).encode("utf-8")
        handler.path = "/workspaces/test/items/capture.dat/batch"
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = BytesIO(payload)
        handler._write_json = Mock()
        handler.do_POST()

        app.start_batch.assert_called_once_with("test", "report", "capture.dat")
        app.batch_status.assert_called_once_with("batch-1")
        handler._write_json.assert_called_once_with(202, started_status)

    def test_batch_cancel_endpoint_requests_cooperative_cancellation(self):
        app = Mock()
        cancelled_status = {
            "id": "batch-1",
            "status": "cancelling",
            "status_url": "/batches/batch-1",
        }
        app.cancel_batch.return_value = cancelled_status
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        payload = b"{}"
        handler.path = "/batches/batch-1/cancel"
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = BytesIO(payload)
        handler._write_json = Mock()

        handler.do_POST()

        app.cancel_batch.assert_called_once_with("batch-1")
        handler._write_json.assert_called_once_with(200, cancelled_status)

    def test_nested_batch_asset_is_served_inline(self):
        app = Mock()
        tile = Path("/tmp/report.assets/4/2_0.png")
        app.batch_file.return_value = tile
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/batches/batch-1/report.assets/4/2_0.png"
        handler._write_export_file = Mock()

        handler.do_GET()

        app.batch_file.assert_called_once_with(
            "batch-1",
            "report.assets/4/2_0.png",
        )
        handler._write_export_file.assert_called_once_with(
            tile,
            inline=True,
        )

    def test_nested_declared_batch_asset_is_served_inline(self):
        app = Mock()
        tile = Path("/tmp/report.assets/4/2_0.png")
        app.declared_batch_file.return_value = tile
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/batch-files/token/report.assets/4/2_0.png"
        handler._write_export_file = Mock()

        handler.do_GET()

        app.declared_batch_file.assert_called_once_with(
            "token",
            "report.assets/4/2_0.png",
        )
        handler._write_export_file.assert_called_once_with(
            tile,
            inline=True,
        )

    def test_batch_result_directory_listing_endpoint(self):
        app = Mock()
        listing = {
            "name": "gallery",
            "path": "/tmp/gallery",
            "entries": [{"name": "frame.png", "kind": "image"}],
        }
        app.batch_directory.return_value = listing
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/batch-browser/job/batch-1/gallery?path=frames"
        handler._write_json = Mock()

        handler.do_GET()

        app.batch_directory.assert_called_once_with(
            "batch-1",
            "gallery",
            "frames",
        )
        handler._write_json.assert_called_once_with(200, listing)

    def test_batch_output_collection_listing_endpoint(self):
        app = Mock()
        listing = {
            "name": "Batch results",
            "path": "/tmp/outputs",
            "entries": [{"name": "frame.png", "kind": "image"}],
        }
        app.batch_outputs.return_value = listing
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/batch-browser/job/batch-1?path=gallery"
        handler._write_json = Mock()

        handler.do_GET()

        app.batch_outputs.assert_called_once_with(
            "batch-1",
            "gallery",
        )
        handler._write_json.assert_called_once_with(200, listing)

    def test_saved_batch_output_collection_listing_endpoint(self):
        app = Mock()
        listing = {
            "name": "Batch results",
            "path": "/tmp/outputs",
            "entries": [{"name": "frame.png", "kind": "image"}],
        }
        app.declared_batch_outputs.return_value = listing
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/batch-browser/saved/token-1?path=gallery"
        handler._write_json = Mock()

        handler.do_GET()

        app.declared_batch_outputs.assert_called_once_with(
            "token-1",
            "gallery",
        )
        handler._write_json.assert_called_once_with(200, listing)

    def test_batch_catalog_endpoint_restores_background_notifications(self):
        app = Mock()
        payload = {
            "jobs": [
                {
                    "id": "batch-1",
                    "status": "running",
                    "status_url": "/batches/batch-1",
                }
            ]
        }
        app.batch_statuses.return_value = payload
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/batches"
        handler._write_json = Mock()

        handler.do_GET()

        app.batch_statuses.assert_called_once_with()
        handler._write_json.assert_called_once_with(200, payload)

    def test_html_batch_result_opens_inline(self):
        app = Mock()
        app.batch_file.return_value = Path("/tmp/report.html")
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/batches/batch-1/report.html"
        handler._write_export_file = Mock()

        handler.do_GET()

        app.batch_file.assert_called_once_with("batch-1", "report.html")
        handler._write_export_file.assert_called_once_with(
            Path("/tmp/report.html"), inline=True
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
                    workspace_modules=(
                        WorkspaceModuleRegistration(module_name, "Workspace", root),
                    ),
                )
                app_identity = id(app)
                self.assertEqual("Before", app.list_workspaces()[0]["name"])

                previous_mtime = module_path.stat().st_mtime_ns
                write_workspace("After")
                os.utime(
                    module_path,
                    ns=(previous_mtime + 1_000_000, previous_mtime + 1_000_000),
                )

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

    def test_soft_refresh_reloads_browser_profile_without_document_navigation(self):
        app = Mock()
        app.config_path = Path("browser.toml")
        app.list_workspaces.return_value = []
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/workspaces?reload=1"
        handler._write_json = Mock()

        handler.do_GET()

        app.reload_browser_profile.assert_called_once_with()
        app.reload_workspace_modules.assert_called_once_with()
        handler._write_json.assert_called_once()


if __name__ == "__main__":
    unittest.main()
