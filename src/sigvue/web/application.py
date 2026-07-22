from __future__ import annotations

# Choreographer launches Chromium through this Python wrapper on Unix so the
# browser inherits its DevTools pipe descriptors. In a PyInstaller build,
# sys.executable is this application rather than a Python interpreter, so route
# that child invocation to the bundled wrapper before importing the web app.
import runpy as _runpy
import sys as _bootstrap_sys

if (
    getattr(_bootstrap_sys, "frozen", False)
    and len(_bootstrap_sys.argv) > 1
    and _bootstrap_sys.argv[1].endswith("_unix_pipe_chromium_wrapper.py")
):
    _bootstrap_sys.argv = _bootstrap_sys.argv[1:]
    _runpy.run_path(_bootstrap_sys.argv[0], run_name="__main__")
    raise SystemExit

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from html import escape as html_escape
import importlib
import inspect
import json
import mimetypes
import shutil
import sys
from tempfile import mkdtemp
import time
from uuid import NAMESPACE_URL, uuid4, uuid5
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from plotly.offline import get_plotlyjs

from sigvue.catalog.browser import filter_items, paginate_items, search_items, sort_items
from sigvue.core.capabilities import Annotation, AnnotationRequest, BatchDestination, BatchResult, ExportRequest
from sigvue.core.models import WorkspaceMetadata
from sigvue.profile import WorkspaceLaunchSpec, load_browser_profile
from sigvue.registry.registry import WorkspaceRegistry
from sigvue.rendering import render_matplotlib_figure
from sigvue.rendering.heatmap import rerasterize_heatmaps
from sigvue.rendering.dispatch import RenderKind, detect_render_kind


_PLOTLY_JS = get_plotlyjs()


_INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__BROWSER_TITLE__</title>
  <style>
    :root { color-scheme: light; --ink:#13212b; --muted:#60717d; --line:#dce5e8; --accent:#087e8b; --wash:#f3f7f7; } html[data-theme="dark"] { color-scheme:dark; --ink:#e7f1f3; --muted:#a9bdc2; --line:#36515b; --accent:#55b9c3; --wash:#193741; } html[data-theme="dark"] body,html[data-theme="dark"] .workspace-sidebar,html[data-theme="dark"] .channel,html[data-theme="dark"] .matplotlib-view,html[data-theme="dark"] .view-switcher-head { background:#10252d } html[data-theme="dark"] .data-toolbar { background:#10252df2 } html[data-theme="dark"] input,html[data-theme="dark"] select,html[data-theme="dark"] .sidebar-toggle,html[data-theme="dark"] .sidebar-close,html[data-theme="dark"] .card,html[data-theme="dark"] .layout-panel,html[data-theme="dark"] .view-choice,html[data-theme="dark"] .view-switcher-select { background:#193741; color:var(--ink); border-color:var(--line) } html[data-theme="dark"] .view-choice.active { background:#164955; color:#e7f1f3; border-color:var(--accent) } html[data-theme="dark"] .card:hover { border-color:var(--accent); box-shadow:none }
    * { box-sizing:border-box } [hidden] { display:none!important } body { margin:0; font:15px/1.5 system-ui,-apple-system,sans-serif; color:var(--ink); background:#fbfcfc }
    header { height:52px; display:flex; align-items:center; gap:8px; padding:0 22px; color:white; background:#102f3a; box-shadow:0 1px 6px #102f3a2b } .header-spacer { flex:1 } header select { min-height:30px; padding:3px 25px 3px 8px; color:#e7f1f3; background:#193741; border-color:#b9d0d54d; font-size:12px } header .sidebar-toggle { min-height:30px; padding:4px 10px; color:#e7f1f3; background:#193741; border-color:#b9d0d54d } header .icon-button { display:grid; width:34px; padding:4px; place-items:center } .icon-button svg { width:18px; height:18px; display:block } .header-nav { display:grid; width:30px; height:30px; padding:0; place-items:center; border:1px solid transparent; border-radius:5px; color:#d8e7ea; background:transparent; cursor:pointer } .header-nav:hover { border-color:#b9d0d54d; background:#ffffff12; color:white } .header-nav:disabled { opacity:.35; cursor:default } .header-nav:disabled:hover { border-color:transparent; background:transparent } .header-nav svg { width:17px; height:17px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round } .fullscreen-toggle { border:1px solid #b9d0d54d; border-radius:5px; padding:3px 8px; background:transparent; color:#e7f1f3; font:18px/1 system-ui,sans-serif; cursor:pointer } .fullscreen-toggle:hover { background:#ffffff1c }
    header b { font-size:16px } header .home-title { all:unset; cursor:pointer; font:700 16px system-ui,sans-serif } header span { color:#b9d0d5; font-size:13px }
    main { width:min(1120px,calc(100% - 36px)); margin:34px auto 80px }
    main.item-page,body.hold-item-layout main { width:calc(100% - 24px); max-width:none; margin:12px auto 0 }
    .crumb { color:var(--muted); margin-bottom:20px } .crumb button { all:unset; cursor:pointer; color:var(--accent) }
    h1 { margin:0 0 6px; font-size:30px; letter-spacing:-.02em } .lead { color:var(--muted); margin:0 0 28px }
    .toolbar { display:flex; gap:10px; margin:24px 0 } input,select { min-height:42px; border:1px solid #bdcbd0; border-radius:7px; padding:8px 12px; background:white; font:inherit }
    input[type=search] { flex:1 } button.primary { border:0; border-radius:7px; padding:10px 15px; color:white; background:var(--accent); font:600 14px inherit; cursor:pointer }
    .list { display:flex; flex-direction:column; gap:10px }
    .item-browser { overflow:hidden; border:1px solid var(--line); border-radius:8px; background:white } html[data-theme="dark"] .item-browser { background:#10252d } .item-browser table { width:100%; border-collapse:collapse; table-layout:fixed } .item-browser th { padding:8px 12px; color:var(--muted); background:var(--wash); border-bottom:1px solid var(--line); font-size:11px; text-align:left; text-transform:uppercase; letter-spacing:.04em } .item-browser th:first-child { width:28% } .item-browser th:last-child { width:18% } .item-browser th button { all:unset; display:flex; width:100%; gap:5px; align-items:center; cursor:pointer } .item-browser th button:hover { color:var(--accent) } .item-browser td { padding:11px 12px; border-bottom:1px solid var(--line); overflow-wrap:anywhere; vertical-align:middle } .item-browser tbody tr:last-child td { border-bottom:0 } .item-browser .item-row,.item-browser .folder-row { cursor:pointer } .item-browser .item-row:hover,.item-browser .folder-row:hover { background:color-mix(in srgb,var(--accent) 7%,transparent) } .item-name { display:flex; flex-direction:column; gap:2px } .item-name small { color:var(--muted) } .item-tags { display:flex; flex-wrap:wrap; gap:3px } .item-tags .tag { margin:0 } .discovery-null { color:var(--muted) }
    .card { display:grid; grid-template-columns:minmax(180px,1fr) 2fr auto auto; align-items:center; gap:18px; border:1px solid var(--line); border-radius:8px; background:white; padding:16px 18px; box-shadow:0 2px 8px #17323c0b; cursor:pointer; transition:.15s }
    .card:hover { border-color:#8eb9bf; box-shadow:0 4px 14px #17323c14 } .card:not(:has(.batch-menu)) { grid-template-columns:minmax(180px,1fr) 2fr auto } .card h2 { font-size:17px; margin:4px 0 } .card p { margin:0 } .card-tags { text-align:right; min-width:130px }
    .muted { color:var(--muted) } .tag { display:inline-block; border-radius:999px; padding:3px 9px; margin:2px 4px 2px 0; font-size:12px; background:#e8f3f3; color:#17626a }
    .batch-menu { position:relative; z-index:5 } .toolbar>.batch-menu { order:2 } .batch-menu summary { position:relative; display:grid; width:34px; height:34px; place-items:center; border:1px solid var(--line); border-radius:50%; color:var(--accent); background:var(--wash); cursor:pointer; list-style:none } .batch-menu summary::-webkit-details-marker { display:none } .batch-menu[open] summary { border-color:var(--accent); box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 14%,transparent) } .batch-play { margin-left:2px; font-size:15px; line-height:1 } .batch-menu-popover { position:absolute; z-index:30; top:40px; right:0; width:320px; padding:8px; border:1px solid var(--line); border-radius:8px; background:#fbfcfc; box-shadow:0 12px 28px #102f3a30 } html[data-theme="dark"] .batch-menu-popover { background:#193741 } .batch-action-row+.batch-action-row { border-top:1px solid var(--line) } .batch-action { display:grid; width:100%; grid-template-columns:1fr auto; gap:8px; padding:9px; border:0; border-radius:6px; color:var(--ink); background:transparent; text-align:left; cursor:pointer } .batch-action:hover { background:var(--wash) } .batch-artifacts { display:grid; gap:5px; padding:0 9px 8px; font-size:12px } .batch-artifact { display:flex; min-width:0; align-items:center; gap:6px } .batch-path { min-width:0; flex:1; overflow:hidden; color:var(--muted); text-overflow:ellipsis; white-space:nowrap } .batch-open { color:var(--accent) } .copy-path { flex:none; padding:3px 7px; border:1px solid var(--line); border-radius:5px; color:var(--ink); background:var(--wash); font:600 11px system-ui,sans-serif; cursor:pointer } .copy-path:hover { border-color:var(--accent); color:var(--accent) } .batch-state { color:var(--muted); font-size:12px } .batch-state.running,.batch-state.pending { color:#b7791f } .batch-state.ready { color:#16803c } .batch-state.error { color:#b42318 } .item-browser th.tags-column { width:18% } .item-browser th.batch-cell,.item-browser td.batch-cell { width:52px; padding-right:8px!important; text-align:right }
    .notification-center { position:relative } .notification-center>summary { display:flex; min-width:34px; min-height:30px; align-items:center; justify-content:center; gap:5px; padding:4px 7px; border:1px solid #b9d0d54d; border-radius:6px; color:#e7f1f3; background:#193741; cursor:pointer; list-style:none } .notification-center>summary::-webkit-details-marker { display:none } .notification-center>summary svg { width:18px; height:18px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round } .notification-center[open]>summary { border-color:#8ed0d7 } .notification-badge { display:grid; min-width:18px; height:18px; padding:0 5px; place-items:center; border-radius:999px; color:#102f3a; background:#8ed0d7; font-size:10px } .notification-popover { position:absolute; z-index:80; top:38px; right:0; width:min(440px,calc(100vw - 24px)); overflow:hidden; border:1px solid var(--line); border-radius:9px; color:var(--ink); background:#fbfcfc; box-shadow:0 14px 32px #071b2240 } html[data-theme="dark"] .notification-popover { background:#193741 } .notification-head { display:flex; align-items:center; justify-content:space-between; padding:11px 13px; border-bottom:1px solid var(--line) } .notification-head strong { font-size:13px } #notification-list { max-height:min(330px,calc(100vh - 120px)); overflow-y:auto; overscroll-behavior:contain } .notification-empty { margin:0; padding:18px; color:var(--muted); font-size:12px; text-align:center } .notification-item { padding:12px 13px; border-bottom:1px solid var(--line) } .notification-item:last-child { border-bottom:0 } .notification-title { display:flex; align-items:start; gap:8px } .notification-title strong { flex:1; color:var(--ink); font-size:13px } .notification-status { flex:none; font-size:11px; font-weight:700; text-transform:uppercase } .notification-status.ready { color:#16803c } .notification-status.error { color:#b42318 } .notification-dismiss { flex:none; padding:0 4px; border:0; color:var(--muted); background:transparent; font-size:18px; line-height:18px; cursor:pointer } .notification-summary { margin:4px 0 0; color:var(--muted); font-size:12px } .notification-files { display:grid; gap:6px; margin-top:9px }
    .notification-toasts { position:fixed; z-index:90; top:60px; right:12px; display:flex; width:min(360px,calc(100vw - 24px)); flex-direction:column; gap:8px; pointer-events:none } .notification-toast { padding:10px 12px; border:1px solid color-mix(in srgb,#16803c 45%,var(--line)); border-radius:8px; color:var(--ink); background:color-mix(in srgb,#16803c 8%,#fbfcfc); box-shadow:0 10px 26px #071b2238; animation:notification-toast-life 3s ease forwards } .notification-toast.error { border-color:color-mix(in srgb,#b42318 45%,var(--line)); background:color-mix(in srgb,#b42318 8%,#fbfcfc) } html[data-theme="dark"] .notification-toast { background:color-mix(in srgb,#16803c 12%,#193741) } html[data-theme="dark"] .notification-toast.error { background:color-mix(in srgb,#b42318 12%,#193741) } .notification-toast strong { display:block; font-size:13px } .notification-toast span { display:block; margin-top:2px; color:var(--muted); font-size:12px } @keyframes notification-toast-life { 0% { opacity:0; transform:translateY(-6px) } 8%,78% { opacity:1; transform:translateY(0) } 100% { opacity:0; transform:translateY(-4px) } }
    .data-toolbar { position:sticky; top:0; z-index:20; display:flex; align-items:center; gap:10px; min-height:46px; margin:0 -12px 4px; padding:6px 16px; background:#fbfcfcf2; border-bottom:1px solid var(--line); backdrop-filter:blur(8px) } .data-toolbar-spacer { flex:1 }
    .playback-bar { display:flex; align-items:center; gap:10px; flex:1; min-width:240px } .playback-bar .primary { padding:6px 10px; min-width:72px } .playback-track { position:relative; display:flex; flex:1; align-items:center; min-width:80px } .playback-track input[type=range] { position:relative; z-index:2; width:100%; min-height:0; padding:0 } .annotation-markers { position:absolute; z-index:0; inset:0 8px; pointer-events:none } .annotation-marker { position:absolute; top:0; bottom:0; width:1px; margin:0; padding:0; border:0; border-radius:0; background:var(--annotation-marker-color,#ffffff); box-shadow:none; opacity:.35; pointer-events:none } .annotation-marker.clustered { width:1px; margin:0; border:0; opacity:.55 } .playback-bar #current-time { flex:none; width:98px; min-height:30px; padding:4px 7px; text-align:right; font:12px ui-monospace,monospace } .playback-bar #counter { width:82px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap }
    .windowed-bar { display:flex; align-items:center; gap:8px; width:100%; min-width:0 } .windowed-track-stack { flex:1; height:30px; min-width:120px } .windowed-label { position:absolute; z-index:4; top:2px; left:6px; max-width:calc(100% - 12px); overflow:hidden; color:var(--muted); font-size:9px; font-weight:600; line-height:10px; text-overflow:ellipsis; text-shadow:0 0 3px var(--wash),0 0 3px var(--wash); white-space:nowrap; pointer-events:none } .windowed-bar .windowed-time,.windowed-bar .windowed-width { flex:none; width:88px; height:30px; min-height:30px; padding:4px 7px; text-align:right; font:12px ui-monospace,monospace } .windowed-total { flex:none; width:82px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap } .windowed-width-label { display:flex; flex:none; min-width:0; align-items:center; gap:7px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap } .windowed-bar .windowed-width { width:118px } .windowed-track { position:relative; width:100%; height:30px; overflow:hidden; border:1px solid var(--line); border-radius:5px; background:var(--wash); touch-action:none } .windowed-overview { position:absolute; z-index:1; inset:2px; width:calc(100% - 4px); height:calc(100% - 4px); pointer-events:none } .windowed-selection { position:absolute; z-index:2; top:0; bottom:0; margin:0; padding:0; border:0; border-radius:0; background:color-mix(in srgb,var(--accent) 22%,transparent); cursor:grab } .windowed-selection:active { cursor:grabbing } .windowed-handle { position:absolute; z-index:3; top:0; bottom:0; width:9px; margin-left:-4px; padding:0; border:0; border-left:2px solid var(--accent); border-right:2px solid var(--accent); border-radius:1px; background:color-mix(in srgb,var(--accent) 45%,transparent); cursor:ew-resize } .windowed-selection:focus-visible,.windowed-handle:focus-visible { outline:2px solid var(--accent); outline-offset:-2px }
    .segmented-bar { display:flex; align-items:center; gap:10px; width:100%; min-width:0 } .segment-actions { display:flex; flex:none; gap:6px } .segment-actions button { min-height:30px; padding:4px 10px; border:1px solid var(--line); border-radius:6px; color:var(--ink); background:var(--wash); font:600 12px inherit; cursor:pointer } .segment-actions button:hover:not(:disabled),.segment-actions button:focus-visible { border-color:var(--accent); color:var(--accent); background:color-mix(in srgb,var(--accent) 10%,var(--wash)) } .segment-actions button:disabled { opacity:.42; cursor:default } .segmented-track { position:relative; flex:1; height:34px; min-width:160px; border:1px solid var(--line); border-radius:5px; background:var(--wash) } .segmented-track::before { position:absolute; top:50%; right:8px; left:8px; height:1px; background:var(--line); content:"" } .segment-marker { position:absolute; z-index:1; top:50%; width:12px; height:12px; margin:-6px 0 0 -6px; padding:0; border:2px solid var(--wash); border-radius:50%; background:var(--muted); box-shadow:0 0 0 1px var(--line); cursor:pointer; transform:scale(.85); transition:transform .12s,background .12s } .segment-marker:hover,.segment-marker:focus-visible { z-index:2; outline:2px solid var(--accent); outline-offset:2px; transform:scale(1.15) } .segment-marker.active { background:var(--accent); box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 35%,transparent); transform:scale(1.25) } .segment-count { flex:none; min-width:54px; color:var(--muted); font:12px ui-monospace,monospace; text-align:right; white-space:nowrap } .segment-time { flex:none; min-width:185px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap }
    .sidebar-toggle,.sidebar-close { border:1px solid var(--line); border-radius:6px; padding:5px 10px; background:white; color:var(--muted); font:600 12px inherit; cursor:pointer } .sidebar-toggle.has-view-parameters { color:var(--accent); border-color:var(--accent) } .workspace-sidebar { position:fixed; z-index:40; top:52px; right:0; bottom:0; display:flex; flex-direction:column; width:min(420px,calc(100vw - 20px)); padding:18px; overflow-y:auto; overflow-x:hidden; background:#fbfcfc; border-left:1px solid var(--line); box-shadow:-10px 0 30px #17323c1c; transform:translateX(102%); transition:transform .18s ease } .workspace-sidebar * { min-width:0 } .workspace-sidebar .table-wrap { overflow:visible; padding:8px 0 } .workspace-sidebar .data-table th,.workspace-sidebar .data-table td { white-space:normal; overflow-wrap:anywhere } .workspace-sidebar.open { transform:translateX(0) } .sidebar-backdrop { position:fixed; z-index:35; inset:52px 0 0; border:0; background:#102f3a24; opacity:0; pointer-events:none; transition:opacity .18s ease } .sidebar-backdrop.open { opacity:1; pointer-events:auto } .sidebar-head { display:flex; align-items:start; gap:12px; padding-bottom:16px; border-bottom:1px solid var(--line) } .sidebar-head .crumb { margin:0 0 7px; font-size:12px } .sidebar-title { min-width:0; flex:1 } .sidebar-title h1 { margin:0; font-size:20px; line-height:1.25 } .sidebar-title .subtitle { display:block; margin-top:4px; color:var(--muted); font-size:13px } .sidebar-close { flex:none; padding:4px 8px } .analysis-panel { display:flex; flex-direction:column; gap:12px; padding-top:16px } .analysis-panel h2 { margin:0; font-size:13px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em } .settings-group { overflow:hidden; border:1px solid var(--line); border-radius:8px; background:white } html[data-theme="dark"] .settings-group { background:#193741 } .settings-group>summary { display:flex; align-items:center; padding:10px 12px; color:var(--ink); cursor:pointer; font-size:12px; font-weight:700; list-style:none } .settings-group>summary::-webkit-details-marker { display:none } .settings-group>summary::after { content:'⌄'; margin-left:auto; color:var(--muted); font-size:16px; transition:transform .15s } .settings-group[open]>summary::after { transform:rotate(180deg) } .settings-group-body { padding:11px 12px 12px; border-top:1px solid var(--line); background:var(--wash) } .view-settings-empty { margin:8px 0 0; color:var(--muted); font-size:12px } .control-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px } .control-fields:empty { display:none } .control-fields label { display:flex; flex-direction:column; gap:3px; color:var(--muted); font-size:11px } .control-fields select,.control-fields input { min-height:34px; padding:5px 8px; color:var(--ink) } .control-fields select { padding-right:26px } .control-fields input[type=number] { width:100% } .control-fields input[type=color] { width:100%; padding:3px; cursor:pointer } .view-stats { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:7px 12px; margin:0; font-size:12px } .view-stats div { display:contents } .view-stats dt { color:var(--muted) } .view-stats dd { margin:0; text-align:right; color:var(--ink); font:12px ui-monospace,monospace; overflow-wrap:anywhere; white-space:normal } .view-stats .runtime-total dt,.view-stats .runtime-total dd { margin-top:4px; padding-top:7px; border-top:1px solid var(--line); color:var(--ink); font-weight:700 }
    .header-menu { position:relative } .header-menu > summary { min-height:30px; padding:4px 10px; border:1px solid #b9d0d54d; border-radius:6px; color:#e7f1f3; background:#193741; font:600 12px/20px system-ui,sans-serif; cursor:pointer; list-style:none } .header-menu > summary::-webkit-details-marker { display:none } .header-menu[open] > summary { border-color:var(--accent) } .header-popover { position:absolute; z-index:60; top:38px; right:0; display:flex; flex-direction:column; gap:10px; width:280px; padding:14px; border:1px solid var(--line); border-radius:8px; color:var(--ink); background:#fbfcfc; box-shadow:0 12px 30px #102f3a33 } html[data-theme="dark"] .header-popover { background:#193741 } .header-popover label { display:flex; flex-direction:column; gap:3px; color:var(--muted); font-size:11px } .header-popover input,.header-popover select,.header-popover textarea { width:100%; min-height:34px; padding:5px 8px; color:var(--ink); background:white; border:1px solid var(--line); border-radius:5px; font:inherit } html[data-theme="dark"] .header-popover input,html[data-theme="dark"] .header-popover select,html[data-theme="dark"] .header-popover textarea { background:#10252d } .header-popover textarea { min-height:70px; resize:vertical } .header-popover .primary { align-self:flex-end; min-height:34px; padding:6px 12px }
    .style-picker-list { display:flex; flex-direction:column; gap:7px; margin-top:9px } .style-picker { overflow:hidden; border:1px solid var(--line); border-radius:8px; background:white } html[data-theme="dark"] .style-picker { background:#193741 } .style-picker summary { display:flex; align-items:center; gap:9px; min-height:40px; padding:7px 10px; cursor:pointer; list-style:none; user-select:none } .style-picker summary::-webkit-details-marker { display:none } .style-picker summary::after { content:'⌄'; margin-left:auto; color:var(--muted); font-size:16px; transition:transform .15s } .style-picker[open] summary::after { transform:rotate(180deg) } .style-swatch { flex:none; width:18px; height:18px; border:2px solid #ffffffcc; border-radius:50%; box-shadow:0 0 0 1px #13212b2e } .style-picker-name { min-width:0; overflow:hidden; color:var(--ink); font-size:13px; font-weight:650; text-overflow:ellipsis; white-space:nowrap } .style-picker-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px 12px; padding:10px; border-top:1px solid var(--line); background:var(--wash) } .style-picker-fields label { display:flex; flex-direction:column; gap:3px; color:var(--muted); font-size:11px } .style-picker-fields input,.style-picker-fields select { width:100%; min-height:34px; padding:5px 8px; color:var(--ink) } .style-picker-fields input[type=color] { padding:3px; cursor:pointer }
    .colormap-picker { overflow:hidden; border:1px solid var(--line); border-radius:8px; background:white } html[data-theme="dark"] .colormap-picker { background:#193741 } .colormap-picker summary { display:grid; grid-template-columns:minmax(90px,1fr) auto auto; align-items:center; gap:9px; min-height:44px; padding:7px 10px; cursor:pointer; list-style:none; user-select:none } .colormap-picker summary::-webkit-details-marker { display:none } .colormap-picker summary::after { content:'⌄'; color:var(--muted); font-size:16px; transition:transform .15s } .colormap-picker[open] summary::after { transform:rotate(180deg) } .colormap-preview { display:block; height:17px; min-width:90px; border:1px solid #13212b2e; border-radius:4px } .colormap-picker-name { overflow:hidden; color:var(--ink); font-size:12px; font-weight:650; text-overflow:ellipsis; white-space:nowrap } .colormap-options { display:flex; flex-direction:column; gap:3px; max-height:280px; overflow:auto; padding:7px; border-top:1px solid var(--line); background:var(--wash) } .colormap-option { display:grid; grid-template-columns:minmax(100px,1fr) 76px; align-items:center; gap:10px; min-height:34px; padding:5px 7px; border:1px solid transparent; border-radius:5px; color:var(--ink); background:transparent; text-align:left; cursor:pointer } .colormap-option:hover,.colormap-option.selected { border-color:var(--accent); background:color-mix(in srgb,var(--accent) 10%,transparent) } .colormap-option .colormap-preview { width:100% }
    .limits-picker { padding:10px; border:1px solid var(--line); border-radius:8px; background:white } html[data-theme="dark"] .limits-picker { background:#193741 } .limits-picker-head { display:grid; grid-template-columns:minmax(0,1fr) 84px 12px 84px; align-items:center; gap:6px; color:var(--muted); font-size:12px } .limits-picker-name { overflow:hidden; color:var(--ink); font-weight:650; text-overflow:ellipsis; white-space:nowrap } .limits-picker-head input { width:100%; min-height:32px; padding:4px 6px; text-align:right; color:var(--ink); font:12px ui-monospace,monospace } .limits-separator { text-align:center }
    .toggle-control { position:relative; display:inline-flex; width:38px; height:22px; align-items:center; cursor:pointer } .toggle-control input { position:absolute; width:1px!important; height:1px; min-height:0!important; margin:0; padding:0!important; opacity:0 } .toggle-track { width:38px; height:22px; border:1px solid var(--line); border-radius:999px; background:var(--wash); transition:.15s } .toggle-track::after { display:block; width:16px; height:16px; margin:2px; border-radius:50%; background:var(--muted); content:""; transition:.15s } .toggle-control input:checked + .toggle-track { border-color:var(--accent); background:color-mix(in srgb,var(--accent) 25%,var(--wash)) } .toggle-control input:checked + .toggle-track::after { background:var(--accent); transform:translateX(16px) } .toggle-control input:focus-visible + .toggle-track { outline:2px solid var(--accent); outline-offset:2px }
    .data-stage { height:400px; min-height:0; overflow:hidden } #active-view,.view { height:100%; min-height:0 } .layout-tabs { position:relative; display:flex; flex-direction:column; height:100%; min-height:0 } .layout-tab-panes { position:relative; flex:1; min-height:0 } .tabs { flex:none; display:flex; gap:4px; overflow-x:auto; border-bottom:1px solid var(--line); margin:0 0 4px; padding-left:4px } .tab { flex:none; border:0; border-bottom:3px solid transparent; background:none; padding:9px 13px 7px; color:var(--muted); font:600 13px inherit; cursor:pointer } .tab.active { color:var(--accent); border-color:var(--accent) } .layout-tab-pane { width:100%; height:100%; min-height:0 } .layout-tab-pane:not(.active) { position:absolute; inset:0; visibility:hidden; pointer-events:none }
    .view h1 { font-size:20px } .view h2 { font-size:16px } .plotly-view { width:100%; height:100%; min-height:0 } .matplotlib-view { display:block; width:100%; height:100%; min-height:0; object-fit:contain; background:white } .playback-grid { display:grid; width:100%; height:100%; min-height:0; grid-template-columns:var(--grid-template,repeat(2,minmax(0,1fr))); grid-template-rows:var(--grid-rows,minmax(0,1fr)); gap:4px } .playback-grid.single-plot { display:block; height:100% } .single-plot .channel { width:100%; height:100%; border:0 } .view-switcher { position:relative; display:flex; flex-direction:column; height:100%; min-height:0 } .view-pane { width:100%; flex:1; min-height:0 } .view-pane:not(.active) { position:absolute; inset:40px 0 0; visibility:hidden; pointer-events:none } .view-switcher-head { position:relative; z-index:2; flex:none; height:40px; display:flex; align-items:center; gap:10px; padding:4px 8px; border-bottom:1px solid var(--line); background:white } .view-switcher-label { color:var(--muted); font-size:12px; font-weight:600 } .view-choice { border:1px solid var(--line); border-radius:5px; background:white; padding:4px 9px; color:var(--muted); font:12px inherit; cursor:pointer } .view-choice.active { border-color:var(--accent); background:#e8f3f3; color:#17626a } .view-switcher-select { min-height:30px; min-width:150px; padding:4px 28px 4px 8px; border:1px solid var(--line); border-radius:5px; background:white; color:var(--ink); font:12px inherit } .parameter-group { flex:none; display:grid; grid-template-columns:repeat(var(--parameter-columns,1),minmax(0,1fr)); gap:9px 12px; padding:10px 12px; border:1px solid var(--line); border-radius:7px; background:var(--wash) } .parameter-group-title { grid-column:1/-1; color:var(--muted); font-size:11px; font-weight:700; letter-spacing:.04em; text-transform:uppercase } .parameter-control { display:flex; flex-direction:column; gap:3px; min-width:0; color:var(--muted); font-size:11px } .parameter-control input,.parameter-control select { width:100%; min-height:34px; padding:5px 8px; color:var(--ink) } .channel { min-width:0; min-height:0; height:100%; overflow:hidden; border-right:1px solid var(--line); border-bottom:1px solid var(--line); background:white } .channel:nth-child(2n) { border-right:0 } .layout-column { display:flex; flex-direction:column; gap:8px; height:100%; min-height:0; overflow:auto } .layout-column > .plotly-view,.layout-column > .matplotlib-view { flex:1 } .layout-row { display:flex; gap:8px; height:100%; min-height:0 } .layout-panel { height:100%; min-height:0; overflow:auto; padding:12px; border:1px solid var(--line); border-radius:7px } .prose,.text-view { padding:16px; color:var(--ink) } .prose h1,.prose h2,.prose h3 { margin:0 0 8px } .table-wrap { overflow:auto; padding:8px } .data-table { width:100%; border-collapse:collapse; font-size:12px } .data-table th { position:sticky; top:0; background:var(--wash); color:var(--muted); text-align:left } .data-table th,.data-table td { padding:7px 9px; border-bottom:1px solid var(--line); white-space:nowrap } .empty,.error { padding:36px; text-align:center; color:var(--muted); border:1px dashed #bac9cd; border-radius:10px }
    .error { color:#8c2e2e; background:#fff7f7 } @media(max-width:700px){header{padding:0 14px}header span{display:none}main{margin-top:20px}main.item-page{width:calc(100% - 12px);margin-top:6px}.toolbar{flex-wrap:wrap}.playback-grid{grid-template-columns:1fr;grid-template-rows:repeat(var(--grid-items),minmax(0,1fr))}.channel{border-right:0}.card{grid-template-columns:1fr}.card-tags{text-align:left}.data-toolbar{flex-wrap:wrap}.workspace-sidebar{width:calc(100vw - 12px);top:52px}.sidebar-backdrop{inset:52px 0 0}.control-fields{grid-template-columns:1fr}}
    .layout-column > .view-switcher { flex:1 }
    .live-toggle { border:1px solid var(--line); border-radius:6px; padding:5px 9px; background:white; color:var(--muted); font:600 12px inherit; cursor:pointer } .live-toggle.active { border-color:#b42318; color:#b42318; background:#fff1f0 } html[data-theme="dark"] .live-toggle { background:#193741; color:var(--muted) } html[data-theme="dark"] .live-toggle.active { border-color:#ff7b72; color:#ff9b94; background:#4a2020 }
    .item-browser:has(.batch-menu) { overflow:visible }
    .batch-menu.pending summary,.batch-menu.running summary { color:#d69e2e; border-color:#d69e2e; background:color-mix(in srgb,#d69e2e 10%,var(--wash)) }
    .batch-menu.ready summary { color:#16803c; border-color:#20a957; background:color-mix(in srgb,#20a957 10%,var(--wash)) }
    .batch-menu.error summary { color:#b42318; border-color:#d13c32; background:color-mix(in srgb,#d13c32 10%,var(--wash)) }
    ::view-transition-old(root),::view-transition-new(root) { animation-duration:100ms; animation-timing-function:ease-out }
  </style>
</head>
<body><header><button class="header-nav" id="header-back" type="button" aria-label="Back" title="Back"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg></button><button class="header-nav" id="header-forward" type="button" aria-label="Forward" title="Forward"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg></button><button class="header-nav" id="header-refresh" type="button" aria-label="Refresh" title="Refresh"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6v5h-5"/><path d="M19 11a8 8 0 1 0 1 5"/></svg></button><button class="home-title" id="app-home">__BROWSER_TITLE__</button><span id="app-subtitle">__BROWSER_SUBTITLE__</span><span class="header-spacer"></span><details class="notification-center" id="header-notifications"><summary aria-label="Notifications" title="Notifications"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></svg><span class="notification-badge" id="notification-badge" hidden>0</span></summary><div class="notification-popover"><div class="notification-head"><strong>Notifications</strong></div><div id="notification-list"><p class="notification-empty">No notifications yet.</p></div></div></details><select id="theme-toggle" aria-label="Color theme"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select><details class="header-menu" id="header-annotate" hidden><summary>Annotate</summary><form class="header-popover" id="annotation-form"></form></details><details class="header-menu" id="header-download" hidden><summary>Download</summary><form class="header-popover" id="download-form"></form></details><button class="sidebar-toggle" id="header-details" data-sidebar-toggle aria-expanded="false" hidden>Details</button><button class="fullscreen-toggle" id="fullscreen-toggle" aria-label="Enter fullscreen" aria-pressed="false">⛶</button></header><div class="notification-toasts" id="notification-toasts" aria-live="polite"></div><main id="app"></main>
<script src="/assets/plotly.min.js"></script>
<script>
const app=document.querySelector('#app');
const appHome=document.querySelector('#app-home');
const appSubtitle=document.querySelector('#app-subtitle');
const headerDetails=document.querySelector('#header-details');
const headerDownload=document.querySelector('#header-download');
const headerAnnotate=document.querySelector('#header-annotate');
const headerBack=document.querySelector('#header-back');
const headerForward=document.querySelector('#header-forward');
const headerRefresh=document.querySelector('#header-refresh');
const headerNotifications=document.querySelector('#header-notifications');
const notificationBadge=document.querySelector('#notification-badge');
const notificationList=document.querySelector('#notification-list');
const notificationToasts=document.querySelector('#notification-toasts');
const fullscreenToggle=document.querySelector('#fullscreen-toggle');
const themeToggle=document.querySelector('#theme-toggle');let themePreference=localStorage.getItem('sigvue-theme')||'system',activeThemeRefresh=null;
function resolvedTheme(){return themePreference==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):themePreference}function applyTheme(){document.documentElement.dataset.theme=resolvedTheme();themeToggle.value=themePreference}async function commitTheme(update){if(!document.startViewTransition){await update();return}const transition=document.startViewTransition(update);await transition.finished}async function refreshTheme(){if(activeThemeRefresh)await activeThemeRefresh();else applyTheme()}applyTheme();themeToggle.onchange=async()=>{themePreference=themeToggle.value;localStorage.setItem('sigvue-theme',themePreference);themeToggle.disabled=true;try{await refreshTheme()}catch(error){applyTheme();alert(`Theme refresh failed: ${error.message}`)}finally{themeToggle.disabled=false}};matchMedia('(prefers-color-scheme: dark)').addEventListener('change',async()=>{if(themePreference==='system'){try{await refreshTheme()}catch(error){applyTheme();console.error(error)}}});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=async path=>{const r=await fetch(path);if(!r.ok)throw new Error((await r.json()).detail||`Request failed (${r.status})`);return r.json()};
const apiPost=async(path,payload)=>{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(!r.ok)throw new Error((await r.json()).detail||`Request failed (${r.status})`);return r.json()};
const nativePushState=history.pushState.bind(history);let routeIndex=Number(history.state?.sigvueIndex??0),routeMaximum=Number(sessionStorage.getItem('sigvue-route-maximum')??routeIndex);if(!Number.isFinite(routeIndex))routeIndex=0;if(!Number.isFinite(routeMaximum)||routeMaximum<routeIndex)routeMaximum=routeIndex;if(history.state?.sigvueIndex==null)history.replaceState({...history.state,sigvueIndex:routeIndex},'',location.href);
function syncHeaderNavigation(){headerBack.disabled=routeIndex<=0;headerForward.disabled=routeIndex>=routeMaximum}
history.pushState=(state,title,path)=>{routeIndex+=1;routeMaximum=routeIndex;sessionStorage.setItem('sigvue-route-maximum',String(routeMaximum));nativePushState({...state,sigvueIndex:routeIndex},title,path);syncHeaderNavigation()};
function pushRoute(path){history.pushState(null,'',path)}
headerBack.onclick=()=>history.back();
headerForward.onclick=()=>history.forward();
headerRefresh.onclick=async()=>{headerRefresh.disabled=true;try{await boot(true)}finally{headerRefresh.disabled=false}};
const fail=e=>app.innerHTML=`<div class="error"><b>Unable to load this page</b><br>${esc(e.message)}</div>`;
let playbackTimer=null,playbackPosition=0,playbackPaused=false,playbackFollowLive=false,windowStart=0,windowEnd=null,segmentId=null,plotResizeObserver=null,windowOverviewResizeObserver=null,redrawWindowOverview=null,dataStageResizeFrame=null,annotations=[],annotationTimelineColorControl=null,activePlaybackSeek=null,activeAnnotationSeek=null;
new MutationObserver(()=>{if(document.body.classList.contains('hold-item-layout'))requestAnimationFrame(()=>document.body.classList.remove('hold-item-layout'))}).observe(app,{childList:true});
const viewSelections={};
const plotSelections=new Map();
const timelineUnits={samples:{seconds:1,label:'samples'},ns:{seconds:1e-9,label:'ns'},us:{seconds:1e-6,label:'µs'},ms:{seconds:1e-3,label:'ms'},s:{seconds:1,label:'s'},min:{seconds:60,label:'min'},h:{seconds:3600,label:'h'},d:{seconds:86400,label:'d'}};
function resolvedTimelineUnit(config){const requested=config.time_unit||'s';if(requested!=='auto')return requested;const duration=Math.abs(Number(config.duration_seconds)||0);if(duration>=172800)return'd';if(duration>=7200)return'h';if(duration>=120)return'min';if(duration>=1)return's';if(duration>=1e-3)return'ms';if(duration>=1e-6)return'us';return'ns'}
function timelineSpec(config){return timelineUnits[resolvedTimelineUnit(config)]||timelineUnits.s}
function displayTime(seconds,config){return Number(seconds)/timelineSpec(config).seconds}
function canonicalTime(value,config){return Number(value)*timelineSpec(config).seconds}
function timeBoxValue(seconds,config){const value=displayTime(seconds,config);return Number.isFinite(value)?Number(value.toPrecision(12)):0}
function formatTimelineTime(seconds,config){const value=displayTime(seconds,config),magnitude=Math.abs(value),digits=magnitude>=1000?2:magnitude>=100?3:magnitude>=1?6:9;return `${Number(value.toFixed(digits))} ${timelineSpec(config).label}`}
function choiceOptions(choices){return (choices||[]).map(choice=>`<option value="${esc(choice.value)}">${esc(choice.label)}</option>`).join('')}
function annotationFieldHtml(field){const required=field.required?'required':'',value=esc(field.default||'');if(field.field_type==='select')return `<label>${esc(field.label)}<select data-annotation-field="${esc(field.name)}" ${required}>${choiceOptions(field.options)}</select></label>`;if(field.field_type==='textarea')return `<label>${esc(field.label)}<textarea data-annotation-field="${esc(field.name)}" ${required}>${value}</textarea></label>`;return `<label>${esc(field.label)}<input ${field.field_type==='number'?'type="number" step="any"':''} data-annotation-field="${esc(field.name)}" value="${value}" ${required}></label>`}
function axisName(value,orientation){const raw=String(value?._name||value?._id||value||'');if(raw===orientation)return`${orientation}axis`;if(new RegExp(`^${orientation}\\d+$`).test(raw))return`${orientation}axis${raw.slice(1)}`;return new RegExp(`^${orientation}axis\\d*$`).test(raw)?raw:null}
function matchedAxisRoot(plot,name){let current=name;const seen=new Set();while(current&&!seen.has(current)){seen.add(current);const match=plot?._fullLayout?.[current]?.matches,next=axisName(match,current[0]);if(!next)return current;current=next}return current||name}
function inferredSelectionAxis(plot,orientation,range,event){const point=(event?.points||[]).find(candidate=>candidate?.[`${orientation}axis`]),fromPoint=axisName(point?.[`${orientation}axis`],orientation);if(fromPoint)return fromPoint;const low=Math.min(...range.map(Number)),high=Math.max(...range.map(Number)),candidates=Object.keys(plot?._fullLayout||{}).filter(name=>new RegExp(`^${orientation}axis\\d*$`).test(name)).filter(name=>{const bounds=plot._fullLayout[name]?.range;if(!Array.isArray(bounds))return false;const minimum=Math.min(...bounds.map(Number)),maximum=Math.max(...bounds.map(Number));return low>=minimum&&high<=maximum});return candidates.length===1?candidates[0]:`${orientation}axis`}
function selectedPlotRanges(plot,event){const result={};for(const [key,range] of Object.entries(event?.range||{})){if(!Array.isArray(range)||range.length<2||!['x','y'].includes(key[0]))continue;const orientation=key[0],explicit=axisName(key,orientation),name=key.length>1&&explicit?explicit:inferredSelectionAxis(plot,orientation,range,event);result[name]=range.map(Number)}return result}
function plotSelectionRange(plot,view,axis){const ranges=plotSelections.get(view);if(!ranges)return null;if(Array.isArray(ranges[axis]))return ranges[axis];const root=matchedAxisRoot(plot,axis);for(const [candidate,range] of Object.entries(ranges)){if(candidate[0]===axis[0]&&matchedAxisRoot(plot,candidate)===root)return range}return null}
function rememberPlotResetRanges(plot,view){const layout=view?.value?.layout||{},update={},bounds={};for(const [name,axis] of Object.entries(layout)){if(!/^[xy]axis\d*$/.test(name)||!Array.isArray(axis?.range)||axis.range.length<2)continue;const range=axis.range.map(Number);if(range.every(Number.isFinite)){update[`${name}.range`]=range;update[`${name}.autorange`]=false;if(view?.axis_navigation==='bounded')bounds[name]=range}}plot._sigvueResetRanges=update;plot._sigvueAxisBounds=bounds}
function changedPlotResetRanges(previous,current){const update={};for(const [key,range] of Object.entries(current||{})){if(!key.endsWith('.range')||!Array.isArray(range))continue;const prior=previous?.[key];if(!Array.isArray(prior)||range.some((value,index)=>value!==prior[index])){update[key]=range;update[key.replace('.range','.autorange')]=false}}return update}
function currentPlotViewport(plot){const viewport={};for(const [name,axis] of Object.entries(plot?._fullLayout||{})){if(!/^[xy]axis\d*$/.test(name)||!Array.isArray(axis?.range)||axis.range.length<2)continue;const range=axis.range.map(Number),reset=plot._sigvueResetRanges?.[`${name}.range`];if(range.every(Number.isFinite)&&Array.isArray(reset)&&range.some((value,index)=>Math.abs(value-reset[index])>1e-12))viewport[name]=range}return viewport}
function restoredPlotViewport(plot,viewport){const update={};for(const [name,range] of Object.entries(viewport||{})){if(!plot?._fullLayout?.[name]||!Array.isArray(range))continue;const bounds=plot._sigvueAxisBounds?.[name],restored=bounds?clampedAxisRange(range,bounds):range;update[`${name}.range`]=restored;update[`${name}.autorange`]=false}return update}
function resetPlotAxes(plot){const update=plot._sigvueResetRanges||{};if(Object.keys(update).length)void Plotly.relayout(plot,update)}
function relayoutAxisRange(event,name){const combined=event?.[`${name}.range`],low=event?.[`${name}.range[0]`],high=event?.[`${name}.range[1]`];return Array.isArray(combined)?combined.map(Number):Number.isFinite(Number(low))&&Number.isFinite(Number(high))?[Number(low),Number(high)]:null}
function clampedAxisRange(range,bounds){const reversed=range[0]>range[1],requested=[Math.min(...range),Math.max(...range)],allowed=[Math.min(...bounds),Math.max(...bounds)],allowedWidth=allowed[1]-allowed[0],width=requested[1]-requested[0];let result;if(width>=allowedWidth)result=allowed;else{let low=requested[0],high=requested[1];if(low<allowed[0]){high+=allowed[0]-low;low=allowed[0]}if(high>allowed[1]){low-=high-allowed[1];high=allowed[1]}result=[low,high]}return reversed?result.reverse():result}
function constrainPlotDuringPan(plot,event){if(plot._sigvueClamping)return;const update={};for(const [name,bounds] of Object.entries(plot._sigvueAxisBounds||{})){const range=relayoutAxisRange(event,name);if(!range)continue;const clamped=clampedAxisRange(range,bounds);if(clamped.some((value,index)=>Math.abs(value-range[index])>1e-12))update[`${name}.range`]=clamped}if(!Object.keys(update).length)return;plot._sigvueClamping=true;Promise.resolve(Plotly.relayout(plot,update)).finally(()=>{plot._sigvueClamping=false})}
function bindPlotSelection(plot,onViewportChanged){if(plot.dataset.plotSelectionBound)return;plot.dataset.plotSelectionBound='true';plot.on('plotly_selected',event=>{const ranges=selectedPlotRanges(plot,event);if(Object.keys(ranges).length)plotSelections.set(plot.dataset.plotView,ranges)});plot.on('plotly_relayouting',event=>constrainPlotDuringPan(plot,event));plot.on('plotly_relayout',()=>onViewportChanged?.());const clear=()=>plotSelections.delete(plot.dataset.plotView);plot.on('plotly_deselect',clear);plot.on('plotly_doubleclick',()=>{clear();resetPlotAxes(plot);return false})}
function annotationBoundPlot(binding){const exact=[...document.querySelectorAll('[data-plot-view]')].find(candidate=>candidate.dataset.plotView===binding.view);if(exact)return exact;const switcher=[...document.querySelectorAll('[data-view-switcher]')].find(candidate=>candidate.dataset.viewSwitcher===binding.view);return switcher?.querySelector(':scope > .view-pane.active [data-plot-view]')||null}
function populatePlotBoundAnnotationFields(page){let populated=0;for(const field of page.annotation?.fields||[]){const binding=field.plot_binding;if(!binding)continue;const plot=annotationBoundPlot(binding),selected=binding.selection_policy==='box_preferred'?plotSelectionRange(plot,plot?.dataset.plotView||binding.view,binding.axis):null,range=selected||plot?._fullLayout?.[binding.axis]?.range;if(!Array.isArray(range)||range.length<2)continue;const edges=range.map(Number).filter(Number.isFinite).sort((a,b)=>a-b),edge=binding.edge==='lower'?edges[0]:edges.at(-1),dynamicOffset=binding.offset_source==='playback'?playbackPosition:0,value=edge*Number(binding.scale??1)+Number(binding.offset??0)+dynamicOffset,input=[...document.querySelectorAll('[data-annotation-field]')].find(candidate=>candidate.dataset.annotationField===field.name);if(input&&Number.isFinite(value)){input.value=Number(value.toPrecision(12));populated++}}return populated}
function configureCapabilityMenus(page){const annotationForm=document.querySelector('#annotation-form'),downloadForm=document.querySelector('#download-form');annotationForm.innerHTML=(page.annotation?.fields||[]).map(annotationFieldHtml).join('')+'<button class="primary" type="submit">Add annotation</button>';headerAnnotate.ontoggle=()=>{if(headerAnnotate.open&&!populatePlotBoundAnnotationFields(page))setTimeout(()=>{if(headerAnnotate.open)populatePlotBoundAnnotationFields(page)},100)};downloadForm.innerHTML=`<label>Data<select id="export-scope">${choiceOptions(page.export?.scopes)}</select></label><label>Format<select id="export-format">${choiceOptions(page.export?.formats)}</select></label><button class="primary" type="submit">Download</button>`}
function markdown(value){return esc(value).replace(/^### (.+)$/gm,'<h3>$1</h3>').replace(/^## (.+)$/gm,'<h2>$1</h2>').replace(/^# (.+)$/gm,'<h1>$1</h1>').replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\n/g,'<br>')}
function plotlyFigure(figure,viewName){const id=`plotly-${encodeURIComponent(viewName)}`;return `<div id="${id}" class="plotly-view" data-plot-view="${esc(viewName)}"></div>`}
function matplotlibFigure(payload,viewName){return `<img class="matplotlib-view" data-matplotlib-view="${esc(viewName)}" alt="${esc(viewName)}" src="data:image/png;base64,${payload}">`}
const plotlyConfig={responsive:true,displaylogo:false,doubleClick:false,modeBarButtonsToAdd:['select2d'],modeBarButtonsToRemove:['lasso2d']};
function setClientRuntime(name,milliseconds){const target=document.querySelector(`[data-client-stat="${name}"]`);if(target)target.textContent=`${milliseconds.toFixed(1)} ms`;if(name==='browser-runtime'){const total=document.querySelector('[data-client-stat="total-runtime"]');if(total)total.textContent=`${(currentServerRuntime+milliseconds).toFixed(1)} ms`}}
function setPlotlyRuntime(started){setClientRuntime('plotly-runtime',performance.now()-started)}
async function initializePlotlyViews(views,onViewportChanged){const started=performance.now(),jobs=[];document.querySelectorAll('[data-plot-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.plotView);if(view&&view.kind==='plotly')jobs.push(Plotly.newPlot(target,view.value.data||[],view.value.layout||{},plotlyConfig).then(()=>{rememberPlotResetRanges(target,view);bindPlotSelection(target,view.rasterized?onViewportChanged:null)}))});await Promise.all(jobs);setPlotlyRuntime(started)}
async function updatePlotlyViews(views){const started=performance.now(),jobs=[];document.querySelectorAll('[data-plot-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.plotView);if(view&&view.kind==='plotly'){const previous=target._sigvueResetRanges,viewport=currentPlotViewport(target);jobs.push(Plotly.react(target,view.value.data||[],view.value.layout||{},plotlyConfig).then(()=>{rememberPlotResetRanges(target,view);const changed=changedPlotResetRanges(previous,target._sigvueResetRanges),restored=restoredPlotViewport(target,viewport),update={...changed,...restored};return Object.keys(update).length?Plotly.relayout(target,update):undefined}))}});await Promise.all(jobs);setPlotlyRuntime(started)}
async function updateMatplotlibViews(views){const jobs=[];document.querySelectorAll('[data-matplotlib-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.matplotlibView);if(view&&view.kind==='matplotlib'){const source=`data:image/png;base64,${view.value}`;jobs.push(new Promise(resolve=>{target.onload=target.onerror=resolve;target.src=source;if(target.complete)resolve()}))}});await Promise.all(jobs)}
async function preloadMatplotlibViews(views){const sources=views.filter(view=>view.kind==='matplotlib').map(view=>`data:image/png;base64,${view.value}`);await Promise.all(sources.map(source=>new Promise(resolve=>{const image=new Image();image.onload=image.onerror=resolve;image.src=source;if(image.complete)resolve()})))}
function resizePlots(){document.querySelectorAll('[data-plot-view]').forEach(target=>Plotly.Plots.resize(target))}
function sizeDataStage(){const stage=document.querySelector('.data-stage');if(!stage)return;const available=Math.max(280,Math.floor(window.innerHeight-stage.getBoundingClientRect().top-4));stage.style.height=`${available}px`;cancelAnimationFrame(dataStageResizeFrame);dataStageResizeFrame=requestAnimationFrame(resizePlots)}
function observeDataStage(){plotResizeObserver?.disconnect();const stage=document.querySelector('.data-stage');if(!stage)return;plotResizeObserver=new ResizeObserver(()=>{cancelAnimationFrame(dataStageResizeFrame);dataStageResizeFrame=requestAnimationFrame(resizePlots)});plotResizeObserver.observe(stage);window.addEventListener('resize',sizeDataStage,{passive:true});sizeDataStage()}
function stopPlayback(){if(app.classList.contains('item-page'))document.body.classList.add('hold-item-layout');clearInterval(playbackTimer);playbackTimer=null;activePlaybackSeek=null;activeAnnotationSeek=null;plotResizeObserver?.disconnect();plotResizeObserver=null;windowOverviewResizeObserver?.disconnect();windowOverviewResizeObserver=null;cancelAnimationFrame(dataStageResizeFrame);dataStageResizeFrame=null;window.removeEventListener('resize',sizeDataStage)}
function syncFullscreenToggle(){const active=Boolean(document.fullscreenElement);fullscreenToggle.setAttribute('aria-label',active?'Exit fullscreen':'Enter fullscreen');fullscreenToggle.setAttribute('aria-pressed',String(active));fullscreenToggle.textContent=active?'×':'⛶';sizeDataStage()}
fullscreenToggle.onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen()}catch(e){/* Browser fullscreen can be unavailable in embedded contexts. */}};
document.addEventListener('fullscreenchange',syncFullscreenToggle);
function tableRows(value){if(Array.isArray(value))return value;if(!value||typeof value!=='object')return[];const columns=Object.keys(value),indices=[...new Set(columns.flatMap(column=>Object.keys(value[column]||{})))];return indices.map(index=>Object.fromEntries(columns.map(column=>[column,value[column]?.[index]])))}
function tableHtml(value){const rows=tableRows(value);if(!rows.length)return '<div class="empty">No rows</div>';const columns=[...new Set(rows.flatMap(row=>Object.keys(row)))];return `<div class="table-wrap"><table class="data-table"><thead><tr>${columns.map(column=>`<th>${esc(column)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${columns.map(column=>`<td>${esc(statText(row[column]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}
function renderValue(v){if(v.kind==='markdown')return `<article class="prose">${markdown(v.value)}</article>`;if(v.kind==='text')return `<div class="text-view">${esc(v.value)}</div>`;if(v.kind==='table'||v.kind==='dataframe')return tableHtml(v.value);return `<pre>${esc(typeof v.value==='string'?v.value:JSON.stringify(v.value,null,2))}</pre>`}
function renderView(v){if(v.kind==='plotly')return plotlyFigure(v.value,v.name);if(v.kind==='matplotlib')return matplotlibFigure(v.value,v.name);return `<div data-render-view="${esc(v.name)}">${renderValue(v)}</div>`}
function updateGenericViews(views){document.querySelectorAll('[data-render-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.renderView);if(view&&view.kind!=='plotly'&&view.kind!=='matplotlib')target.innerHTML=renderValue(view)})}
function gridTemplate(columns){if(Array.isArray(columns))return columns.map(weight=>`minmax(0,${Number(weight)||1}fr)`).join(' ');const count=Math.max(1,Number(columns)||1);return `repeat(${count},minmax(0,1fr))`}
function renderLayout(node,views,controls,values){if(node.kind==='view_slot'){const view=views.find(v=>v.name===node.view);return view?renderView(view):''}if(node.kind==='control_slot'){const control=controls.find(candidate=>candidate.name===node.props.name);return control?(control.control_type==='colormap'?colormapPickerHtml(control,values):`<label class="parameter-control">${esc(control.label||controlLabel(control.name))}${controlHtml(control,values)}</label>`):''}if(node.kind==='tabs'){const labels=node.children.map((child,i)=>child.props.label||`Tab ${i+1}`);return `<div class="layout-tabs" data-layout-tabs><nav class="tabs">${labels.map((label,i)=>`<button class="tab ${i===0?'active':''}" data-layout-tab="${i}">${esc(label)}</button>`).join('')}</nav><div class="layout-tab-panes">${node.children.map((child,i)=>`<div class="layout-tab-pane ${i===0?'active':''}" data-layout-pane="${i}" aria-hidden="${i!==0}">${renderLayout(child,views,controls,values)}</div>`).join('')}</div></div>`}if(node.kind==='view_switcher'){const key=String(node.props.key),dimensionLabels=Array.isArray(node.props.labels)?node.props.labels:[node.props.label||'View'],selectors=Array.isArray(node.props.selectors)?node.props.selectors:[node.props.selector||'buttons'],options=Array.isArray(node.props.options)?node.props.options:[node.children.map((child,i)=>child.props.label||`View ${i+1}`)],coordinates=Array.isArray(node.props.coordinates)?node.props.coordinates:node.children.map((_,i)=>[i]),selectionKeys=Array.isArray(node.props.selection_keys)?node.props.selection_keys:[key],selected=selectionKeys.map(selectionKey=>Number(viewSelections[selectionKey]||0)),control=dimension=>selectors[dimension]==='dropdown'?`<select class="view-switcher-select" data-view-select data-view-dimension="${dimension}">${options[dimension].map((choice,i)=>`<option value="${i}" ${i===selected[dimension]?'selected':''}>${esc(choice)}</option>`).join('')}</select>`:options[dimension].map((choice,i)=>`<button class="view-choice ${i===selected[dimension]?'active':''}" data-view-choice="${i}" data-view-dimension="${dimension}">${esc(choice)}</button>`).join(''),active=coordinate=>coordinate.every((choice,dimension)=>Number(choice)===selected[dimension]);return `<div class="view-switcher" data-view-switcher="${esc(key)}" data-view-selection-keys="${esc(selectionKeys.join(','))}"><div class="view-switcher-head">${dimensionLabels.map((dimensionLabel,dimension)=>`<span class="view-switcher-label">${esc(dimensionLabel)}</span>${control(dimension)}`).join('')}</div>${node.children.map((child,i)=>`<div class="view-pane ${active(coordinates[i])?'active':''}" data-view-pane="${i}" data-view-coordinates="${esc(coordinates[i].join(','))}" data-view-label="${esc(child.props.label||`View ${i+1}`)}" aria-hidden="${!active(coordinates[i])}">${renderLayout(child,views,controls,values)}</div>`).join('')}</div>`}const children=node.children.map(child=>renderLayout(child,views,controls,values)).join('');if(node.kind==='control_group')return `<div class="parameter-group" style="--parameter-columns:${Number(node.props.columns)||1}">${node.props.label?`<div class="parameter-group-title">${esc(node.props.label)}</div>`:''}${children}</div>`;if(node.kind==='grid'){const columnCount=Array.isArray(node.props.columns)?node.props.columns.length:Number(node.props.columns)||1,rowCount=Math.ceil(node.children.length/columnCount);return `<div class="playback-grid ${node.children.length===1?'single-plot':''}" style="--grid-template:${gridTemplate(node.props.columns)};--grid-rows:repeat(${rowCount},minmax(0,1fr));--grid-items:${node.children.length}">${node.children.map(child=>`<div class="channel">${renderLayout(child,views,controls,values)}</div>`).join('')}</div>`}if(node.kind==='column'||node.kind==='stack')return `<div class="layout-column">${children}</div>`;if(node.kind==='row')return `<div class="layout-row">${children}</div>`;if(node.kind==='panel')return `<div class="layout-panel">${children}</div>`;return children}
function bindLayoutTabs(){document.querySelectorAll('[data-layout-tabs]').forEach(root=>{const buttons=root.querySelectorAll(':scope > .tabs > [data-layout-tab]'),panes=root.querySelectorAll(':scope > .layout-tab-panes > [data-layout-pane]');buttons.forEach(button=>button.onclick=()=>{const selected=Number(button.dataset.layoutTab);buttons.forEach((candidate,index)=>candidate.classList.toggle('active',index===selected));panes.forEach((pane,index)=>{pane.classList.toggle('active',index===selected);pane.setAttribute('aria-hidden',String(index!==selected))});requestAnimationFrame(resizePlots)})})}
function bindViewSwitchers(){document.querySelectorAll('.view-switcher[data-view-switcher]').forEach(root=>{const selectionKeys=String(root.dataset.viewSelectionKeys||root.dataset.viewSwitcher).split(','),selected=dimension=>Number(viewSelections[selectionKeys[dimension]]||0),activate=(dimension,value)=>{viewSelections[selectionKeys[dimension]]=value;root.querySelectorAll(':scope > .view-switcher-head [data-view-choice]').forEach(choice=>choice.classList.toggle('active',Number(choice.dataset.viewDimension)===dimension?Number(choice.dataset.viewChoice)===value:choice.classList.contains('active')));root.querySelectorAll(':scope > .view-switcher-head [data-view-select]').forEach(select=>{if(Number(select.dataset.viewDimension)===dimension)select.value=value});root.querySelectorAll(':scope > [data-view-pane]').forEach(pane=>{const coordinate=String(pane.dataset.viewCoordinates||pane.dataset.viewPane).split(',').map(Number),active=coordinate.every((choice,index)=>choice===selected(index));pane.classList.toggle('active',active);pane.setAttribute('aria-hidden',String(!active))});redrawWindowOverview?.();requestAnimationFrame(resizePlots)};root.querySelectorAll(':scope > .view-switcher-head [data-view-choice]').forEach(button=>button.onclick=()=>activate(Number(button.dataset.viewDimension||0),Number(button.dataset.viewChoice)));root.querySelectorAll(':scope > .view-switcher-head [data-view-select]').forEach(select=>select.onchange=()=>activate(Number(select.dataset.viewDimension||0),Number(select.value)))})}
function annotationAppliesToView(annotation){return Object.entries(annotation.view_selections||{}).every(([key,index])=>Number(viewSelections[key]||0)===Number(index))}
function annotationMarkerGroups(config,maximum=120){const duration=Math.max(0,Number(config.duration_seconds)||0);if(!duration)return[];const groups=new Map();for(const annotation of annotations){if(!annotationAppliesToView(annotation))continue;const position=Number(annotation.position_seconds);if(!Number.isFinite(position)||position<0||position>duration)continue;const bin=Math.min(maximum-1,Math.floor(position/duration*maximum));if(!groups.has(bin))groups.set(bin,[]);groups.get(bin).push(annotation)}return [...groups.values()]}
function annotationMarkerColor(){const control=[...document.querySelectorAll('[data-control]')].find(candidate=>candidate.dataset.control===annotationTimelineColorControl),color=control?.value;return /^#[0-9a-f]{6}$/i.test(color||'')?color:'#ffffff'}
function updateAnnotationMarkerColor(){const color=annotationMarkerColor();document.querySelectorAll('[data-annotation-markers]').forEach(target=>target.style.setProperty('--annotation-marker-color',color))}
function renderAnnotationMarkers(config){const duration=Math.max(0,Number(config.duration_seconds)||0),first=annotations[0],last=annotations.at(-1),selectionSignature=Object.entries(viewSelections).sort().map(([key,index])=>`${key}:${index}`).join(','),signature=`${duration}|${annotations.length}|${first?.id||''}|${last?.id||''}|${selectionSignature}`;let groups=null;document.querySelectorAll('[data-annotation-markers]').forEach(target=>{target.style.setProperty('--annotation-marker-color',annotationMarkerColor());if(target.dataset.annotationSignature===signature)return;target.dataset.annotationSignature=signature;groups??=annotationMarkerGroups(config);target.innerHTML=groups.map(group=>{const first=group[0],position=Number(first.position_seconds),percent=duration?position/duration*100:0,detail=[first.label||'Annotation',formatTimelineTime(position,config),first.comment].filter(Boolean).join(' · '),label=group.length===1?detail:`${group.length} annotations · ${formatTimelineTime(position,config)} · ${detail}`;return `<span class="annotation-marker ${group.length>1?'clustered':''}" style="left:${percent}%" data-annotation-position="${position}" data-annotation-count="${group.length}" aria-label="${esc(label)}" title="${esc(label)}"></span>`}).join('')})}
function startFrameworkPlayback(config,refresh){
  const bar=document.querySelector('#playback-bar');if(!bar)return;clearInterval(playbackTimer);if(playbackPosition>config.duration_seconds)playbackPosition=0;
  const slider=bar.querySelector('#position'),current=bar.querySelector('#current-time'),counter=bar.querySelector('#counter'),live=bar.querySelector('#jump-live');let updating=false;
  const updateClock=()=>{const spec=timelineSpec(config);slider.max=config.duration_seconds;current.max=displayTime(config.duration_seconds,config);current.step=displayTime(config.step_seconds,config);current.setAttribute('aria-label',`Current playback time in ${spec.label}`);slider.value=playbackPosition;current.value=timeBoxValue(playbackPosition,config);counter.textContent=`/ ${formatTimelineTime(config.duration_seconds,config)}`;live?.classList.toggle('active',playbackFollowLive);renderAnnotationMarkers(config)};
  const update=async()=>{if(updating)return;updating=true;try{await refresh()}finally{updating=false}};
  const seek=async(value,displayValue=false)=>{const parsed=displayValue?canonicalTime(value,config):Number(value);if(!Number.isFinite(parsed)){updateClock();return}playbackFollowLive=false;playbackPosition=Math.min(config.duration_seconds,Math.max(0,parsed));updateClock();await update()};
  activePlaybackSeek=seek;
  activeAnnotationSeek=seek;
  slider.step=config.step_seconds;slider.oninput=e=>seek(e.target.value);
  current.onchange=e=>seek(e.target.value,true);current.onkeydown=e=>{if(e.key==='Enter')e.currentTarget.blur()};
  if(live)live.onclick=async()=>{playbackFollowLive=true;playbackPaused=false;bar.querySelector('#toggle').textContent='❚❚ Pause';await update();playbackPosition=config.duration_seconds;updateClock()};
  bar.querySelector('#toggle').onclick=()=>{playbackPaused=!playbackPaused;bar.querySelector('#toggle').textContent=playbackPaused?'▶ Play':'❚❚ Pause'};
  updateClock();const interval=config.refresh_interval_seconds??config.step_seconds;playbackTimer=setInterval(async()=>{if(playbackPaused||updating)return;if(playbackFollowLive){await update();playbackPosition=config.duration_seconds;updateClock();return}playbackPosition+=config.step_seconds;if(playbackPosition>config.duration_seconds)playbackPosition=config.loop?0:config.duration_seconds;updateClock();await update()},interval*1000)
}
function startFrameworkWindowed(config,refresh){
  windowOverviewResizeObserver?.disconnect();redrawWindowOverview=null;const root=document.querySelector('#windowed-bar'),track=root?.querySelector('#windowed-track');if(!root||!track)return;const duration=Number(config.duration_seconds)||0,minimum=Math.max(Number(config.minimum_window_seconds)||0,1e-12),step=Number(config.step_seconds)||minimum;
  if(windowEnd==null){windowStart=Number(config.window_start_seconds)||0;windowEnd=Number(config.window_end_seconds)||Math.min(duration,windowStart+minimum)}
  const canvas=track.querySelector('canvas'),selection=track.querySelector('#windowed-selection'),left=track.querySelector('#windowed-left'),right=track.querySelector('#windowed-right'),startInput=root.querySelector('#windowed-start'),endInput=root.querySelector('#windowed-end'),totalLabel=root.querySelector('#windowed-total'),widthInput=root.querySelector('#windowed-width'),unitLabel=root.querySelector('#windowed-unit');let drag=null,updating=false,pending=false,commitTimer=null;
  const clamp=()=>{windowStart=Math.min(duration-minimum,Math.max(0,Number(windowStart)||0));windowEnd=Math.min(duration,Math.max(windowStart+minimum,Number(windowEnd)||minimum))};
  const selectedOverviewIndex=()=>{const series=config.overview_series||[];return Math.min(Math.max(0,Number(viewSelections[config.overview_switcher_key]||0)),Math.max(0,series.length-1))};
  const selectedOverview=()=>{const series=config.overview_series||[];return series[selectedOverviewIndex()]||config.overview_values||[]};
  const selectedDuration=()=>{const durations=config.overview_durations_seconds||[],value=Number(durations[selectedOverviewIndex()]);return Number.isFinite(value)&&value>0?value:duration};
  const displayedWindow=()=>{const available=selectedDuration(),width=Math.min(available,windowEnd-windowStart),start=Math.min(Math.max(0,available-width),Math.max(0,windowStart));return[start,start+width]};
  const drawOverview=()=>{const rect=canvas.getBoundingClientRect(),ratio=devicePixelRatio||1,width=Math.max(1,Math.round(rect.width*ratio)),height=Math.max(1,Math.round(rect.height*ratio));if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height}const context=canvas.getContext('2d'),values=selectedOverview().map(Number).filter(Number.isFinite);context.clearRect(0,0,width,height);if(values.length<2)return;const limits=values.reduce((result,value)=>[Math.min(result[0],value),Math.max(result[1],value)],[Infinity,-Infinity]),low=limits[0],span=limits[1]-low||1,style=getComputedStyle(document.documentElement);context.beginPath();values.forEach((value,index)=>{const x=index/(values.length-1)*width,y=height-2-(value-low)/span*(height-4);if(index)context.lineTo(x,y);else context.moveTo(x,y)});context.strokeStyle=style.getPropertyValue('--accent').trim();context.lineWidth=Math.max(1,ratio);context.stroke()};
  const render=()=>{clamp();const spec=timelineSpec(config),displayDuration=selectedDuration(),[displayStart,displayEnd]=displayedWindow(),leftPercent=displayDuration?displayStart/displayDuration*100:0,rightPercent=displayDuration?displayEnd/displayDuration*100:100;selection.style.left=`${leftPercent}%`;selection.style.width=`${rightPercent-leftPercent}%`;left.style.left=`${leftPercent}%`;right.style.left=`${rightPercent}%`;startInput.value=timeBoxValue(displayStart,config);endInput.value=timeBoxValue(displayEnd,config);startInput.max=endInput.max=displayTime(displayDuration,config);widthInput.min=displayTime(Math.min(minimum,displayDuration),config);widthInput.max=displayTime(displayDuration,config);startInput.step=endInput.step=widthInput.step=displayTime(step,config);startInput.setAttribute('aria-label',`Window start time in ${spec.label}`);endInput.setAttribute('aria-label',`Window stop time in ${spec.label}`);widthInput.setAttribute('aria-label',`Window width in ${spec.label}`);totalLabel.textContent=`/ ${formatTimelineTime(displayDuration,config)}`;widthInput.value=timeBoxValue(displayEnd-displayStart,config);unitLabel.textContent=`${spec.label} buffer`;left.setAttribute('aria-valuenow',String(displayStart));right.setAttribute('aria-valuenow',String(displayEnd));drawOverview();renderAnnotationMarkers(config)};
  redrawWindowOverview=render;
  const commit=async()=>{if(updating){pending=true;return}updating=true;try{await refresh()}finally{updating=false;if(pending){pending=false;void commit()}}};
  const scheduleCommit=()=>{if(commitTimer!==null)return;commitTimer=setTimeout(()=>{commitTimer=null;void commit()},75)};
  const finalCommit=()=>{if(commitTimer!==null){clearTimeout(commitTimer);commitTimer=null}void commit()};
  const begin=(kind,event)=>{event.preventDefault();const[start,end]=displayedWindow();drag={kind,pointer:event.pointerId,x:event.clientX,start,end};track.setPointerCapture(event.pointerId)};
  left.onpointerdown=event=>begin('left',event);right.onpointerdown=event=>begin('right',event);selection.onpointerdown=event=>begin('move',event);
  track.onpointermove=event=>{if(!drag||drag.pointer!==event.pointerId)return;const available=selectedDuration(),localMinimum=Math.min(minimum,available),delta=(event.clientX-drag.x)/Math.max(1,track.clientWidth)*available;if(drag.kind==='left'){windowStart=Math.min(drag.end-localMinimum,Math.max(0,drag.start+delta));windowEnd=drag.end}else if(drag.kind==='right'){windowStart=drag.start;windowEnd=Math.max(drag.start+localMinimum,Math.min(available,drag.end+delta))}else{const width=drag.end-drag.start;windowStart=Math.min(available-width,Math.max(0,drag.start+delta));windowEnd=windowStart+width}render();scheduleCommit()};
  track.onpointerup=event=>{if(!drag||drag.pointer!==event.pointerId)return;drag=null;track.releasePointerCapture(event.pointerId);finalCommit()};track.onpointercancel=()=>{drag=null;finalCommit()};
  const editEndpoint=(kind,value)=>{const parsed=canonicalTime(value,config);if(!Number.isFinite(parsed)){render();return}const[start,end]=displayedWindow();if(kind==='start'){windowStart=parsed;windowEnd=end}else{windowStart=start;windowEnd=parsed}render();finalCommit()};
  const editWidth=value=>{const parsed=canonicalTime(value,config),available=selectedDuration();if(!Number.isFinite(parsed)||parsed<=0){render();return}const[start]=displayedWindow(),target=Math.min(available,Math.max(Math.min(minimum,available),parsed));windowStart=start;windowEnd=windowStart+target;if(windowEnd>available){windowEnd=available;windowStart=Math.max(0,available-target)}render();finalCommit()};
  activeAnnotationSeek=value=>{const position=Math.max(0,Math.min(duration,Number(value)||0)),width=windowEnd-windowStart;windowStart=Math.min(Math.max(0,duration-width),position);windowEnd=Math.min(duration,windowStart+width);render();finalCommit()};
  startInput.onchange=event=>editEndpoint('start',event.target.value);endInput.onchange=event=>editEndpoint('end',event.target.value);widthInput.onchange=event=>editWidth(event.target.value);startInput.onkeydown=endInput.onkeydown=widthInput.onkeydown=event=>{if(event.key==='Enter')event.currentTarget.blur()};
  const keyboard=(kind,event)=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const available=selectedDuration(),localMinimum=Math.min(minimum,available),[start,end]=displayedWindow(),delta=event.key==='ArrowLeft'?-step:step;if(kind==='left'){windowStart=Math.min(end-localMinimum,Math.max(0,start+delta));windowEnd=end}else if(kind==='right'){windowStart=start;windowEnd=Math.max(start+localMinimum,Math.min(available,end+delta))}else{const width=end-start;windowStart=Math.min(available-width,Math.max(0,start+delta));windowEnd=windowStart+width}render();void commit()};
  left.onkeydown=event=>keyboard('left',event);right.onkeydown=event=>keyboard('right',event);selection.onkeydown=event=>keyboard('move',event);windowOverviewResizeObserver=new ResizeObserver(render);windowOverviewResizeObserver.observe(track);render()
}
function startFrameworkSegmented(config,refresh){
  const root=document.querySelector('#segmented-bar'),track=root?.querySelector('#segmented-track'),previous=root?.querySelector('#segment-previous'),next=root?.querySelector('#segment-next'),counter=root?.querySelector('#segment-count'),time=root?.querySelector('#segment-time');if(!root||!track)return;let updating=false;
  const available=()=>config.segments||[];
  const selectedIndex=()=>Math.max(0,available().findIndex(segment=>segment.identifier===segmentId));
  const render=()=>{const segments=available();if(!segments.length)return;if(!segments.some(segment=>segment.identifier===segmentId))segmentId=config.selected_segment_id||segments[0].identifier;const index=selectedIndex(),selected=segments[index],duration=Number(config.duration_seconds)||0;track.innerHTML=`<div class="annotation-markers" data-annotation-markers></div>`+segments.map(segment=>{const percent=duration?Math.min(100,Math.max(0,Number(segment.start_seconds)/duration*100)):0,label=segment.label||segment.identifier,title=`${label} · ${formatTimelineTime(segment.start_seconds,config)} · ${formatTimelineTime(segment.duration_seconds,config)}`;return `<button class="segment-marker ${segment.identifier===segmentId?'active':''}" type="button" style="left:${percent}%" data-segment-id="${esc(segment.identifier)}" aria-label="${esc(title)}" title="${esc(title)}"></button>`}).join('');counter.textContent=`${index+1} / ${segments.length}`;time.textContent=`${formatTimelineTime(selected.start_seconds,config)}–${formatTimelineTime(Number(selected.start_seconds)+Number(selected.duration_seconds),config)} / ${formatTimelineTime(duration,config)}`;previous.disabled=index===0;next.disabled=index===segments.length-1;track.querySelectorAll('[data-segment-id]').forEach(marker=>marker.onclick=()=>select(marker.dataset.segmentId));renderAnnotationMarkers(config)};
  const select=async identifier=>{if(updating||identifier===segmentId)return;segmentId=identifier;render();updating=true;try{await refresh()}finally{updating=false;render()}};
  activeAnnotationSeek=value=>{const position=Number(value)||0,segments=available(),nearest=segments.reduce((best,segment)=>Math.abs(Number(segment.start_seconds)-position)<Math.abs(Number(best.start_seconds)-position)?segment:best,segments[0]);if(nearest)void select(nearest.identifier)};
  previous.onclick=()=>{const segments=available(),index=selectedIndex();if(index>0)void select(segments[index-1].identifier)};next.onclick=()=>{const segments=available(),index=selectedIndex();if(index<segments.length-1)void select(segments[index+1].identifier)};render()
}
function startFrameworkRefresh(config,refresh){let updating=false;playbackTimer=setInterval(async()=>{if(updating)return;updating=true;try{await refresh()}finally{updating=false}},config.interval_seconds*1000)}
const controlLabel=name=>name.split('_').map(x=>x[0].toUpperCase()+x.slice(1)).join(' ');
function controlHtml(control,values){const value=values[control.name]??control.default;if(control.control_type==='toggle')return `<span class="toggle-control"><input type="checkbox" data-control="${esc(control.name)}" ${String(value).toLowerCase()==='true'?'checked':''}><span class="toggle-track"></span></span>`;if(control.control_type==='select')return `<select data-control="${esc(control.name)}">${control.options.map(option=>`<option value="${esc(option)}" ${String(value)===String(option)?'selected':''}>${esc(option)}</option>`).join('')}</select>`;if(control.control_type==='color')return `<input type="color" data-control="${esc(control.name)}" value="${esc(value)}">`;if(control.control_type==='integer'||control.control_type==='float')return `<input type="number" data-control="${esc(control.name)}" value="${esc(value)}" ${control.minimum==null?'':`min="${esc(control.minimum)}"`} ${control.maximum==null?'':`max="${esc(control.maximum)}"`} ${control.step==null?'':`step="${esc(control.step)}"`}>`;return `<input data-control="${esc(control.name)}" value="${esc(value)}">`}
function controlFieldHtml(control,values){return `<label>${esc(control.label||controlLabel(control.name))}${controlHtml(control,values)}</label>`}
function stylePickerHtml(controls,values){const color=controls.find(control=>control.control_type==='color'),value=color?(values[color.name]??color.default):'#60717d',label=controls.find(control=>control.picker_label)?.picker_label||controlLabel(controls[0].picker);return `<details class="style-picker" data-style-picker="${esc(controls[0].picker)}"><summary><span class="style-swatch" data-style-swatch style="background:${esc(value)}"></span><span class="style-picker-name">${esc(label)}</span></summary><div class="style-picker-fields">${controls.map(control=>controlFieldHtml(control,values)).join('')}</div></details>`}
const colormapGradient=colors=>`linear-gradient(90deg,${colors.join(',')})`;
function colormapPickerHtml(control,values){const value=String(values[control.name]??control.default),index=Math.max(0,control.options.findIndex(option=>String(option)===value)),colors=(control.option_previews||[])[index]||[],gradient=colormapGradient(colors);return `<details class="colormap-picker" data-colormap-picker><summary><span class="colormap-preview" data-colormap-preview style="background:${esc(gradient)}"></span><span class="colormap-picker-name">${esc(value)}</span></summary><input type="hidden" data-control="${esc(control.name)}" value="${esc(value)}"><div class="colormap-options">${control.options.map((option,optionIndex)=>{const optionGradient=colormapGradient((control.option_previews||[])[optionIndex]||[]);return `<button class="colormap-option ${String(option)===value?'selected':''}" type="button" data-colormap-option="${esc(option)}" data-colormap-gradient="${esc(optionGradient)}"><span class="colormap-preview" style="background:${esc(optionGradient)}"></span><span>${esc(option)}</span></button>`}).join('')}</div></details>`}
function bindColormapPickers(){document.querySelectorAll('[data-colormap-picker]').forEach(picker=>{const input=picker.querySelector('[data-control]'),preview=picker.querySelector('[data-colormap-preview]'),name=picker.querySelector('.colormap-picker-name');picker.querySelectorAll('[data-colormap-option]').forEach(option=>option.onclick=()=>{input.value=option.dataset.colormapOption;preview.style.background=option.dataset.colormapGradient;name.textContent=option.dataset.colormapOption;picker.querySelectorAll('[data-colormap-option]').forEach(candidate=>candidate.classList.toggle('selected',candidate===option));picker.open=false;input.dispatchEvent(new Event('change',{bubbles:true}))})})}
function limitsValue(control,values){const fallback=control.default.map(Number),raw=values[control.name]??fallback,parts=Array.isArray(raw)?raw:String(raw).split(',',2),minimum=Number(control.minimum),maximum=Number(control.maximum);let lower=Number(parts[0]),upper=Number(parts[1]);if(!Number.isFinite(lower)||!Number.isFinite(upper)||lower>=upper)[lower,upper]=fallback;lower=Math.max(minimum,Math.min(maximum,lower));upper=Math.max(minimum,Math.min(maximum,upper));return lower<upper?[lower,upper]:fallback}
function limitsPickerHtml(control,values){const [lower,upper]=limitsValue(control,values),minimum=Number(control.minimum),maximum=Number(control.maximum),step=Number(control.step)||1;return `<div class="limits-picker" data-limits-picker><div class="limits-picker-head"><span class="limits-picker-name">${esc(control.label||controlLabel(control.name))}</span><input type="number" data-limit-number="lower" value="${lower}" min="${minimum}" max="${maximum}" step="${step}" aria-label="Lower limit"><span class="limits-separator">to</span><input type="number" data-limit-number="upper" value="${upper}" min="${minimum}" max="${maximum}" step="${step}" aria-label="Upper limit"></div><input type="hidden" data-control="${esc(control.name)}" value="${lower},${upper}"></div>`}
function bindLimitsPickers(){document.querySelectorAll('[data-limits-picker]').forEach(picker=>{const hidden=picker.querySelector('[data-control]'),lowerNumber=picker.querySelector('[data-limit-number="lower"]'),upperNumber=picker.querySelector('[data-limit-number="upper"]'),minimum=Number(lowerNumber.min),maximum=Number(lowerNumber.max),step=Number(lowerNumber.step)||1;const update=(changed,value)=>{let lower=Number(lowerNumber.value),upper=Number(upperNumber.value),next=Number(value);if(!Number.isFinite(next))next=changed==='lower'?lower:upper;if(changed==='lower')lower=Math.min(upper-step,Math.max(minimum,next));else upper=Math.max(lower+step,Math.min(maximum,next));lowerNumber.value=lower;upperNumber.value=upper;hidden.value=`${lower},${upper}`;hidden.dispatchEvent(new Event('change',{bubbles:true}))};lowerNumber.onchange=event=>update('lower',event.target.value);upperNumber.onchange=event=>update('upper',event.target.value);lowerNumber.onkeydown=upperNumber.onkeydown=event=>{if(event.key==='Enter')event.currentTarget.blur()}})}
function customControlHtml(control,values){return control.control_type==='colormap'?colormapPickerHtml(control,values):limitsPickerHtml(control,values)}
function controlGroupHtml(controls,values){const special=controls.filter(control=>['colormap','limits'].includes(control.control_type)),regular=controls.filter(control=>!control.picker&&!['colormap','limits'].includes(control.control_type)),pickers=controls.filter(control=>control.picker).reduce((result,control)=>{(result[control.picker]??=[]).push(control);return result},{});const custom=[...special.map(control=>customControlHtml(control,values)),...Object.values(pickers).map(picker=>stylePickerHtml(picker,values))];return `<div class="control-fields">${regular.map(control=>controlFieldHtml(control,values)).join('')}</div>${custom.length?`<div class="style-picker-list">${custom.join('')}</div>`:''}`}
const statText=value=>value!=null&&typeof value==='object'?JSON.stringify(value):String(value??'—');
function statisticsRows(statistics){return Object.entries(statistics||{}).map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(statText(value))}</dd></div>`).join('')}
let currentServerRuntime=0;
function runtimeMilliseconds(value){const parsed=Number.parseFloat(String(value||''));return Number.isFinite(parsed)?parsed:0}
function runtimeRows(statistics){currentServerRuntime=runtimeMilliseconds(statistics?.['Server total']);const preparation=statistics?.['Workspace total']||statistics?.['Analysis runtime']||'—',views=statistics?.['View callbacks']||'—';return `<div><dt>Data & analysis</dt><dd>${esc(preparation)}</dd></div><div><dt>View generation</dt><dd>${esc(views)}</dd></div><div><dt>Browser rendering</dt><dd data-client-stat="browser-runtime">—</dd></div><div class="runtime-total"><dt>Total</dt><dd data-client-stat="total-runtime">—</dd></div>`}
function sidebarHtml(workspaceName,page){const details=page.controls.filter(control=>control.placement!=='inline'),groups=details.reduce((result,control)=>{const label=control.group||'Analysis settings';(result[label]??=[]).push(control);return result},{}),settings=Object.entries(groups).map(([label,controls])=>`<details class="settings-group" open><summary>${esc(label)}</summary><div class="settings-group-body">${controlGroupHtml(controls,page.control_values)}</div></details>`).join('');return `<button class="sidebar-backdrop" data-sidebar-backdrop aria-label="Close details"></button><aside class="workspace-sidebar" data-workspace-sidebar aria-label="Workspace details"><div class="sidebar-head"><div class="sidebar-title"><div class="crumb"><button id="home">Workspaces</button> / <button id="back">${esc(workspaceName)}</button></div><h1>${esc(page.title)}</h1><span class="subtitle">${esc(page.subtitle||'')}</span></div><button class="sidebar-close" data-sidebar-close aria-label="Close details">Close</button></div><div class="analysis-panel">${settings}<section><h2>View details</h2><dl class="view-stats" id="view-stats">${statisticsRows(page.statistics)}</dl></section><section><h2>Runtime</h2><dl class="view-stats" id="runtime-stats">${runtimeRows(page.runtime_statistics)}</dl></section></div></aside>`}
function updateStatistics(statistics,runtimeStatistics){const viewTarget=document.querySelector('#view-stats'),runtimeTarget=document.querySelector('#runtime-stats');if(viewTarget)viewTarget.innerHTML=statisticsRows(statistics);if(runtimeTarget)runtimeTarget.innerHTML=runtimeRows(runtimeStatistics)}
function bindSidebar(){const sidebar=document.querySelector('[data-workspace-sidebar]'),backdrop=document.querySelector('[data-sidebar-backdrop]'),toggle=document.querySelector('[data-sidebar-toggle]');if(!sidebar||!backdrop||!toggle)return;const setOpen=open=>{sidebar.classList.toggle('open',open);backdrop.classList.toggle('open',open);toggle.setAttribute('aria-expanded',String(open))};toggle.onclick=()=>setOpen(!sidebar.classList.contains('open'));backdrop.onclick=()=>setOpen(false);sidebar.querySelector('[data-sidebar-close]').onclick=()=>setOpen(false)}
const batchState=action=>action?.status||'idle';
const batchStatusGlyph=action=>({running:'●',pending:'●',ready:'✓',error:'!'})[batchState(action)]||'';
const batchLauncherHtml=action=>`<span class="batch-play" aria-hidden="true">▶</span>`;
function batchArtifactHtml(file){return `<div class="batch-artifact"><span class="batch-path" title="${esc(file.path)}">${esc(file.path)}</span>${file.open_url?`<a class="batch-open" href="${esc(file.open_url)}" target="_blank" rel="noopener">Open</a>`:''}<button class="copy-path" type="button" data-copy-path="${esc(file.path)}">Copy path</button></div>`}
function batchMenuHtml(batch,url){if(!batch?.enabled)return '';const summary=batch.actions.find(action=>['running','pending'].includes(batchState(action)))||batch.actions.find(action=>batchState(action)==='ready')||batch.actions.find(action=>batchState(action)==='error')||batch.actions[0];return `<details class="batch-menu ${esc(batchState(summary))}" data-batch-menu data-batch-url="${esc(url)}"><summary title="Run batch action" aria-label="Run batch action">${batchLauncherHtml(summary)}</summary><div class="batch-menu-popover">${batch.actions.map(action=>`<div class="batch-action-row"><button class="batch-action" type="button" data-batch-action="${esc(action.value)}"><span>${esc(action.label)}</span><span class="batch-state ${esc(batchState(action))}">${batchStatusGlyph(action)} ${esc(batchState(action))}</span></button>${action.files?.length?`<div class="batch-artifacts">${action.files.map(batchArtifactHtml).join('')}</div>`:''}</div>`).join('')}</div></details>`}
async function copyText(value){if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(value);return}const input=document.createElement('textarea');input.value=value;input.style.position='fixed';input.style.opacity='0';document.body.append(input);input.select();const copied=document.execCommand('copy');input.remove();if(!copied)throw new Error('Clipboard access is unavailable')}
function bindCopyPaths(root=document){root.querySelectorAll('[data-copy-path]').forEach(button=>button.onclick=async event=>{event.preventDefault();event.stopPropagation();const label=button.textContent;try{await copyText(button.dataset.copyPath);button.textContent='Copied'}catch(error){button.textContent='Copy failed'}finally{setTimeout(()=>button.textContent=label,1200)}})}
const notifications=[];
function renderNotifications(){notificationBadge.hidden=!notifications.length;notificationBadge.textContent=String(notifications.length);notificationList.innerHTML=notifications.length?notifications.map(notification=>`<article class="notification-item" data-notification="${esc(notification.notificationId)}"><div class="notification-title"><span class="notification-status ${esc(notification.status)}">${esc(notification.status)}</span><strong>${notification.status==='ready'?'Batch complete':'Batch failed'}</strong><button class="notification-dismiss" type="button" data-dismiss-notification="${esc(notification.notificationId)}" aria-label="Dismiss">×</button></div><p class="notification-summary">${esc(notification.summary||notification.detail||'')}</p>${notification.files?.length?`<div class="notification-files">${notification.files.map(batchArtifactHtml).join('')}</div>`:''}</article>`).join(''):'<p class="notification-empty">No notifications yet.</p>';bindCopyPaths(notificationList);notificationList.querySelectorAll('[data-dismiss-notification]').forEach(button=>button.onclick=event=>{event.preventDefault();event.stopPropagation();const index=notifications.findIndex(item=>item.notificationId===button.dataset.dismissNotification);if(index>=0)notifications.splice(index,1);renderNotifications()})}
function showBatchResult(status){notifications.unshift({...status,notificationId:`${Date.now()}-${Math.random()}`});renderNotifications();const toast=document.createElement('div');toast.className=`notification-toast ${status.status==='error'?'error':''}`;toast.innerHTML=`<strong>${status.status==='ready'?'Batch complete':'Batch failed'}</strong><span>${esc(status.summary||status.detail||'')}</span>`;notificationToasts.append(toast);setTimeout(()=>toast.remove(),3000)}
function closeBatchMenus(except=null){document.querySelectorAll('[data-batch-menu][open]').forEach(menu=>{if(menu!==except)menu.open=false})}
headerNotifications.onclick=event=>{event.stopPropagation();closeBatchMenus()};
document.addEventListener('click',()=>{closeBatchMenus();headerNotifications.open=false});
function bindBatchMenus(){document.querySelectorAll('[data-batch-menu]').forEach(menu=>{menu.onclick=event=>{event.stopPropagation();headerNotifications.open=false;if(event.target.closest('summary'))closeBatchMenus(menu)};bindCopyPaths(menu);menu.querySelectorAll('[data-batch-action]').forEach(button=>button.onclick=async event=>{event.preventDefault();event.stopPropagation();const state=button.querySelector('.batch-state');state.className='batch-state running';state.textContent='● running';menu.className='batch-menu running';menu.querySelector('summary').innerHTML=batchLauncherHtml({status:'running'});menu.open=false;try{const started=await apiPost(menu.dataset.batchUrl,{action:button.dataset.batchAction});let status=started;while(['pending','running'].includes(status.status)){await new Promise(resolve=>setTimeout(resolve,500));status=await api(started.status_url)}state.className=`batch-state ${status.status}`;state.textContent=`${batchStatusGlyph(status)} ${status.status}`;menu.className=`batch-menu ${status.status}`;menu.querySelector('summary').innerHTML=batchLauncherHtml(status);showBatchResult(status)}catch(error){state.className='batch-state error';state.textContent='! error';menu.className='batch-menu error';menu.querySelector('summary').innerHTML=batchLauncherHtml({status:'error'});showBatchResult({status:'error',detail:error.message})}})})}
async function catalog(navigate=true){stopPlayback();activeThemeRefresh=null;headerDetails.hidden=true;headerDownload.hidden=true;headerDownload.open=false;headerAnnotate.hidden=true;headerAnnotate.open=false;app.className='';if(navigate)pushRoute('/');try{const initial=await api('/workspaces'),workspaces=initial.workspaces;app.innerHTML=`<h1>Workspaces</h1><p class="lead">Choose a workspace, or run one of its batch actions in the background.</p><div class="toolbar"><input id="workspace-search" type="search" placeholder="Search workspaces…" aria-label="Search workspaces"></div><div class="list" id="workspaces"></div>`;const draw=()=>{const q=document.querySelector('#workspace-search').value.toLowerCase().trim();const shown=workspaces.filter(w=>!q||`${w.name} ${w.description||''} ${w.category||''} ${(w.tags||[]).join(' ')} ${w.id}`.toLowerCase().includes(q));document.querySelector('#workspaces').innerHTML=shown.length?shown.map(w=>`<article class="card" data-id="${esc(w.id)}"><div><span class="tag">${esc(w.category||'workspace')}</span><h2>${esc(w.name)}</h2></div><p class="muted">${esc(w.description)}</p><div class="card-tags">${w.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div>${batchMenuHtml(w.batch,`/workspaces/${encodeURIComponent(w.id)}/batch`)}</article>`).join(''):'<div class="empty">No matching workspaces.</div>';document.querySelectorAll('[data-id]').forEach(x=>x.onclick=()=>items(x.dataset.id,workspaces.find(w=>w.id===x.dataset.id).name));bindBatchMenus()};draw();document.querySelector('#workspace-search').oninput=draw}catch(e){fail(e)}}
function siDiscoveryValue(value,unit){const number=Number(value);if(!Number.isFinite(number))return String(value);const magnitude=Math.abs(number),prefixes=[[1e12,'T'],[1e9,'G'],[1e6,'M'],[1e3,'k'],[1,''],[1e-3,'m'],[1e-6,'µ'],[1e-9,'n']];const [scale,prefix]=prefixes.find(([scale])=>magnitude>=scale)||[1,''];return `${Number((number/scale).toPrecision(4))} ${prefix}${unit||''}`.trim()}
function discoveryValue(value,column){if(value==null||value==='')return '<span class="discovery-null">—</span>';if(column.kind==='datetime'){const date=new Date(value);return esc(Number.isNaN(date.valueOf())?value:date.toLocaleString())}if(column.kind==='si')return esc(siDiscoveryValue(value,column.unit));return esc(value)}
function discoverySortValue(item,key,kind){const value=key==='title'?item.title:item.summary_fields?.[key];if(value==null||value==='')return null;if(['number','si'].includes(kind))return Number(value);if(kind==='datetime')return new Date(value).valueOf();return String(value).toLocaleLowerCase()}
async function items(id,name,navigate=true,directory=[]){stopPlayback();activeThemeRefresh=null;headerDetails.hidden=true;headerDownload.hidden=true;headerDownload.open=false;headerAnnotate.hidden=true;headerAnnotate.open=false;app.className='';const route=directory.length?`/workspace/${encodeURIComponent(id)}/browse/${directory.map(encodeURIComponent).join('/')}`:`/workspace/${encodeURIComponent(id)}`;if(navigate)history.pushState(null,'',route);try{const params=new URLSearchParams();directory.forEach(segment=>params.append('directory',segment));const listing=await api(`/workspaces/${encodeURIComponent(id)}/items?${params}`),list=listing.items,folders=listing.directories,columns=listing.columns||[],crumbs=directory.map((segment,index)=>` / <button data-directory-level="${index+1}">${esc(segment)}</button>`).join('');let sortKey='title',sortDescending=false;app.innerHTML=`<div class="crumb"><button id="home">Workspaces</button> / <button id="workspace-root">${esc(name)}</button>${crumbs}</div><h1>${esc(directory.at(-1)||name)}</h1><p class="lead">Browse items or dispatch their batch actions without opening them.</p><div class="toolbar">${batchMenuHtml(listing.batch,`/workspaces/${encodeURIComponent(id)}/batch`)}<input id="search" type="search" placeholder="Search this folder…"></div><div id="items"></div>`;const draw=()=>{const q=document.querySelector('#search').value.toLowerCase().trim(),shownFolders=folders.filter(folder=>!q||folder.name.toLowerCase().includes(q)),matching=list.filter(item=>!q||`${item.title} ${item.subtitle||''} ${(item.tags||[]).join(' ')} ${item.source_reference||''} ${Object.values(item.summary_fields||{}).filter(value=>value!=null).join(' ')}`.toLowerCase().includes(q)),sortColumn=columns.find(column=>column.key===sortKey),kind=sortColumn?.kind||'text',shown=[...matching].sort((left,right)=>{const a=discoverySortValue(left,sortKey,kind),b=discoverySortValue(right,sortKey,kind);if(a==null)return b==null?0:1;if(b==null)return-1;const result=typeof a==='number'&&typeof b==='number'?a-b:String(a).localeCompare(String(b));return sortDescending?-result:result}),columnCount=columns.length+3,header=(key,label)=>`<th><button type="button" data-sort="${esc(key)}">${esc(label)}${sortKey===key?` <span aria-hidden="true">${sortDescending?'▼':'▲'}</span>`:''}</button></th>`,folderRows=shownFolders.map(folder=>`<tr class="folder-row" data-folder="${folders.indexOf(folder)}"><td colspan="${columnCount}"><span class="tag">folder</span> <strong>${esc(folder.name)}</strong></td></tr>`).join(''),itemRows=shown.map(item=>`<tr class="item-row" data-item="${esc(item.id)}"><td><div class="item-name"><strong>${esc(item.title)}</strong>${item.subtitle?`<small>${esc(item.subtitle)}</small>`:''}</div></td>${columns.map(column=>`<td>${discoveryValue(item.summary_fields?.[column.key],column)}</td>`).join('')}<td><div class="item-tags">${(item.tags||[]).map(tag=>`<span class="tag">${esc(tag)}</span>`).join('')}</div></td><td class="batch-cell">${batchMenuHtml(item.batch,`/workspaces/${encodeURIComponent(id)}/items/${encodeURIComponent(item.id)}/batch`)}</td></tr>`).join('');document.querySelector('#items').innerHTML=folderRows||itemRows?`<div class="item-browser"><table><thead><tr>${header('title','Name')}${columns.map(column=>header(column.key,column.label)).join('')}<th class="tags-column">Tags</th><th class="batch-cell">Run</th></tr></thead><tbody>${folderRows}${itemRows}</tbody></table></div>`:'<div class="empty">No matching items.</div>';document.querySelectorAll('[data-sort]').forEach(button=>button.onclick=()=>{const key=button.dataset.sort;if(sortKey===key)sortDescending=!sortDescending;else{sortKey=key;sortDescending=false}draw()});document.querySelectorAll('[data-folder]').forEach(element=>element.onclick=()=>items(id,name,true,folders[Number(element.dataset.folder)].path));document.querySelectorAll('[data-item]').forEach(element=>element.onclick=()=>openItem(id,name,element.dataset.item));bindBatchMenus()};draw();bindBatchMenus();document.querySelector('#home').onclick=()=>catalog();document.querySelector('#workspace-root').onclick=()=>items(id,name,true,[]);document.querySelectorAll('[data-directory-level]').forEach(element=>element.onclick=()=>items(id,name,true,directory.slice(0,Number(element.dataset.directoryLevel))));document.querySelector('#search').oninput=draw}catch(e){fail(e)}}
async function openItem(wid,wname,iid,navigate=true,controlValues={},preservePlayback=false){
  stopPlayback();app.innerHTML='<div class="empty">Opening item…</div>';app.className='item-page';activeThemeRefresh=null;headerDetails.hidden=true;headerDownload.hidden=true;headerDownload.open=false;headerAnnotate.hidden=true;headerAnnotate.open=false;if(!preservePlayback){playbackPosition=0;playbackPaused=false;playbackFollowLive=false;windowStart=0;windowEnd=null;segmentId=null;Object.keys(viewSelections).forEach(key=>delete viewSelections[key])}if(navigate)history.pushState(null,'',`/workspace/${encodeURIComponent(wid)}/item/${encodeURIComponent(iid)}`);
  try{const request=async values=>api(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}?${new URLSearchParams(values)}`),windowValues=()=>windowEnd==null?{}:{__window_start_seconds:windowStart,__window_end_seconds:windowEnd},segmentValues=()=>segmentId==null?{}:{__segment_id:segmentId};let data=await request({...controlValues,...windowValues(),...segmentValues(),__theme:resolvedTheme(),__playback_time_seconds:playbackPosition});let p=data.page,requestGeneration=0;const isPlayback=['seek','live'].includes(p.playback.mode),isWindowed=p.playback.mode==='windowed',isSegmented=p.playback.mode==='segmented';annotations=p.annotation?.entries||[];
    const playbackConfig=p.playback;annotationTimelineColorControl=p.annotation?.timeline_color_control||null;if(isWindowed&&windowEnd==null){windowStart=Number(playbackConfig.window_start_seconds)||0;windowEnd=Number(playbackConfig.window_end_seconds)||playbackConfig.duration_seconds}if(isSegmented)segmentId=playbackConfig.selected_segment_id;headerDetails.hidden=false;headerDownload.hidden=!p.export?.enabled;headerAnnotate.hidden=!p.annotation?.enabled;configureCapabilityMenus(p);const hasWindowOverview=(playbackConfig.overview_values||[]).length>0,playbackToolbar=isPlayback?`<div class="data-toolbar"><div class="playback-bar" id="playback-bar"><button class="primary" id="toggle">${playbackPaused?'▶ Play':'❚❚ Pause'}</button><div class="playback-track"><input id="position" aria-label="Playback position" type="range" min="0" value="${playbackPosition}"><div class="annotation-markers" data-annotation-markers aria-label="Annotations"></div></div><input id="current-time" aria-label="Current playback time in seconds" type="number" min="0" step="any" value="${playbackPosition}"><span id="counter"></span>${playbackConfig.mode==='live'?'<button class="live-toggle" id="jump-live">Live</button>':''}</div></div>`:isWindowed?`<div class="data-toolbar"><div class="windowed-bar" id="windowed-bar"><input class="windowed-time" id="windowed-start" aria-label="Window start time in seconds" type="number" min="0" max="${playbackConfig.duration_seconds}" step="any"><div class="windowed-track-stack ${hasWindowOverview?'has-overview':''}"><div class="windowed-track" id="windowed-track">${hasWindowOverview?`<span class="windowed-label" title="${esc(playbackConfig.overview_label||'Signal overview')}">${esc(playbackConfig.overview_label||'Signal overview')}</span>`:''}<canvas class="windowed-overview" aria-hidden="true"></canvas><div class="annotation-markers" data-annotation-markers aria-label="Annotations"></div><button class="windowed-selection" id="windowed-selection" type="button" aria-label="Move selected window"></button><button class="windowed-handle" id="windowed-left" type="button" role="slider" aria-label="Window start" aria-valuemin="0" aria-valuemax="${playbackConfig.duration_seconds}"></button><button class="windowed-handle" id="windowed-right" type="button" role="slider" aria-label="Window end" aria-valuemin="0" aria-valuemax="${playbackConfig.duration_seconds}"></button></div></div><input class="windowed-time" id="windowed-end" aria-label="Window stop time in seconds" type="number" min="0" max="${playbackConfig.duration_seconds}" step="any"><span class="windowed-total" id="windowed-total"></span><label class="windowed-width-label"><input class="windowed-width" id="windowed-width" aria-label="Window buffer width in seconds" type="number" min="${playbackConfig.minimum_window_seconds}" max="${playbackConfig.duration_seconds}" step="any"> <span id="windowed-unit">s buffer</span></label></div></div>`:isSegmented?`<div class="data-toolbar"><div class="segmented-bar" id="segmented-bar"><div class="segmented-track" id="segmented-track" aria-label="Available result segments"></div><span class="segment-count" id="segment-count"></span><span class="segment-time" id="segment-time"></span><div class="segment-actions"><button id="segment-previous" type="button">Previous</button><button id="segment-next" type="button">Next</button></div></div></div>`:'';app.innerHTML=`${playbackToolbar}${sidebarHtml(wname,p)}<section class="data-stage"><div id="active-view" class="view"></div></section>`;
    let rasterRefreshTimer=null,lastRasterViewportSignature='{}';const plotViewportPayload=()=>Object.fromEntries([...document.querySelectorAll('[data-plot-view]')].map(plot=>[plot.dataset.plotView,currentPlotViewport(plot)]).filter(([,viewport])=>Object.keys(viewport).length));const scheduleRasterRefresh=()=>{clearTimeout(rasterRefreshTimer);rasterRefreshTimer=setTimeout(()=>{const signature=JSON.stringify(plotViewportPayload());if(signature===lastRasterViewportSignature)return;lastRasterViewportSignature=signature;void refresh(true,false,true)},180)};
    const browserStarted=performance.now(),segmentActions=document.querySelector('.segment-actions'),segmentedBar=document.querySelector('.segmented-bar');if(segmentActions&&segmentedBar)segmentedBar.prepend(segmentActions);document.querySelector('#active-view').innerHTML=renderLayout(p.layout,p.rendered_views,p.controls,p.control_values);bindLayoutTabs();bindViewSwitchers();bindColormapPickers();bindLimitsPickers();bindSidebar();observeDataStage();void Promise.all([initializePlotlyViews(p.rendered_views,scheduleRasterRefresh),preloadMatplotlibViews(p.rendered_views)]).then(()=>setClientRuntime('browser-runtime',performance.now()-browserStarted));requestAnimationFrame(resizePlots);
    const selected=()=>({...Object.fromEntries([...document.querySelectorAll('[data-control]')].map(c=>[c.dataset.control,c.type==='checkbox'?String(c.checked):c.value])),...Object.fromEntries(Object.entries(viewSelections).map(([key,index])=>[`__view_selection_${key}`,index])),...windowValues(),...segmentValues(),__theme:resolvedTheme(),__playback_follow_live:playbackFollowLive,__plot_viewports:JSON.stringify(plotViewportPayload())});
    const annotationPosition=()=>isWindowed?windowStart:isSegmented?Number((playbackConfig.segments||[]).find(segment=>segment.identifier===segmentId)?.start_seconds||0):playbackPosition;
    const annotationDuration=()=>isWindowed?windowEnd-windowStart:isSegmented?Number((playbackConfig.segments||[]).find(segment=>segment.identifier===segmentId)?.duration_seconds||0):null;
    document.querySelector('#annotation-form').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget,button=form.querySelector('button'),values=Object.fromEntries([...form.querySelectorAll('[data-annotation-field]')].map(field=>[field.dataset.annotationField,field.value]));button.disabled=true;button.textContent='Saving…';try{const result=await apiPost(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}/annotations`,{control_values:{...selected(),__playback_time_seconds:playbackPosition},position_seconds:annotationPosition(),duration_seconds:annotationDuration(),values});annotations.push(result);renderAnnotationMarkers(playbackConfig);headerAnnotate.open=false;form.reset();try{await refresh(true)}catch(refreshError){alert(`Annotation saved, but the plots could not refresh: ${refreshError.message}`)}}catch(error){alert(`Annotation failed: ${error.message}`)}finally{button.disabled=false;button.textContent='Add annotation'}};
    document.querySelector('#download-form').onsubmit=async event=>{event.preventDefault();const button=event.currentTarget.querySelector('button'),scope=document.querySelector('#export-scope').value,format=document.querySelector('#export-format').value;button.disabled=true;button.textContent='Preparing…';try{const job=await apiPost(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}/exports`,{control_values:{...selected(),__playback_time_seconds:playbackPosition},scope,format});let status;do{await new Promise(resolve=>setTimeout(resolve,350));status=await api(job.status_url)}while(status.status==='pending'||status.status==='running');if(status.status==='error')throw new Error(status.detail);for(const file of status.files){const link=document.createElement('a');link.href=file.url;link.download=file.name;link.click()}headerDownload.open=false}catch(error){alert(`Export failed: ${error.message}`)}finally{button.disabled=false;button.textContent='Download'}};
    const refresh=async(includeStatic=false,commitRequestedTheme=false,rasterOnly=false)=>{const generation=++requestGeneration,result=await request({...selected(),__playback_time_seconds:playbackPosition,__include_static_views:includeStatic});if(generation!==requestGeneration)return false;const browserStarted=performance.now();data=result;Object.assign(playbackConfig,result.page.playback);p=result.page;p.playback=playbackConfig;annotationTimelineColorControl=p.annotation?.timeline_color_control||null;if(Array.isArray(p.annotation?.entries))annotations=p.annotation.entries;if(playbackFollowLive)playbackPosition=playbackConfig.duration_seconds;if(commitRequestedTheme)await preloadMatplotlibViews(p.rendered_views);const render=async()=>{if(commitRequestedTheme)applyTheme();if(!rasterOnly)updateStatistics(p.statistics,p.runtime_statistics);await updatePlotlyViews(p.rendered_views);if(!rasterOnly){await updateMatplotlibViews(p.rendered_views);updateGenericViews(p.rendered_views)}setClientRuntime('browser-runtime',performance.now()-browserStarted)};if(commitRequestedTheme)await commitTheme(render);else await render();return true};activeThemeRefresh=async()=>{if(isPlayback)clearInterval(playbackTimer);const applied=await refresh(true,true);if(isPlayback&&applied)startFrameworkPlayback(p.playback,refresh);else if(isWindowed&&applied)startFrameworkWindowed(p.playback,refresh);else if(isSegmented&&applied)startFrameworkSegmented(p.playback,refresh)};
    const settingsChanged=async()=>{if(isPlayback)clearInterval(playbackTimer);const applied=await refresh(true);if(isPlayback&&applied)startFrameworkPlayback(p.playback,refresh);else if(isWindowed&&applied)startFrameworkWindowed(p.playback,refresh);else if(isSegmented&&applied)startFrameworkSegmented(p.playback,refresh)};
    if(isPlayback)startFrameworkPlayback(p.playback,refresh);else if(isWindowed)startFrameworkWindowed(p.playback,refresh);else if(isSegmented)startFrameworkSegmented(p.playback,refresh);else if(p.refresh.enabled)startFrameworkRefresh(p.refresh,refresh);document.querySelectorAll('[data-control]').forEach(x=>{x.onchange=settingsChanged;if(x.type==='color')x.oninput=()=>{const swatch=x.closest('[data-style-picker]')?.querySelector('[data-style-swatch]');if(swatch)swatch.style.background=x.value;updateAnnotationMarkerColor()}});document.querySelector('#home').onclick=()=>catalog();document.querySelector('#back').onclick=()=>items(wid,wname,true,data.item.navigation_path||[])
  }catch(e){fail(e)}}
async function boot(reload=false){const parts=location.pathname.split('/').filter(Boolean).map(decodeURIComponent),workspaceUrl=reload?'/workspaces?reload=1':'/workspaces';if(parts[0]!=='workspace'){if(reload)await api(workspaceUrl);return catalog(false)}try{const {workspaces}=await api(workspaceUrl),workspace=workspaces.find(w=>w.id===parts[1]);if(!workspace)return catalog(false);if(parts[2]==='item'&&parts[3])return openItem(workspace.id,workspace.name,parts[3],false);if(parts[2]==='browse')return items(workspace.id,workspace.name,false,parts.slice(3));return items(workspace.id,workspace.name,false)}catch(e){fail(e)}}
appHome.onclick=()=>catalog();
window.onpopstate=event=>{routeIndex=Number(event.state?.sigvueIndex??0);syncHeaderNavigation();boot()};syncHeaderNavigation();boot();
</script></body></html>"""


@dataclass(frozen=True)
class WorkspaceModuleRegistration:
    module_name: str
    attribute: str
    watch_path: Path | None = None
    configuration: dict[str, Any] = field(default_factory=dict)
    metadata_overrides: dict[str, Any] = field(default_factory=dict)


class _ConfiguredWorkspace:
    """Delegate analysis behavior while giving one profile entry its own identity."""

    def __init__(self, workspace: Any, overrides: dict[str, Any]) -> None:
        self._workspace = workspace
        metadata = workspace.metadata
        self.metadata = WorkspaceMetadata(
            identifier=overrides.get("identifier", metadata.identifier),
            display_name=overrides.get("display_name", metadata.display_name),
            description=overrides.get("description", metadata.description),
            version=metadata.version,
            category=overrides.get("category", metadata.category),
            tags=overrides.get("tags", metadata.tags),
            icon=overrides.get("icon", metadata.icon),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._workspace, name)


@dataclass
class ExportJob:
    directory: Path
    future: Future[dict[str, object]]


@dataclass
class BatchJob:
    workspace_id: str
    item_id: str | None
    action: str
    directory: Path
    future: Future[dict[str, object]]
    temporary: bool = True


def _item_payload(item: Any) -> dict[str, Any]:
    return {
        "id": item.identifier,
        "title": item.title,
        "subtitle": item.subtitle,
        "source_reference": item.source_reference,
        "timestamp": item.timestamp.isoformat() if item.timestamp else None,
        "tags": list(item.tags),
        "navigation_path": list(item.navigation_path),
        "summary_fields": item.summary_fields,
    }


@dataclass
class SigvueApp:
    title: str = "Sigvue"
    subtitle: str = "Explore scientific and analytical results"
    registry: WorkspaceRegistry | None = None
    reload_workspaces: bool = False
    workspace_modules: tuple[WorkspaceModuleRegistration, ...] = ()
    config_path: Path | None = None
    _fixed_workspaces: list[Any] = field(default_factory=list, init=False, repr=False)
    _workspace_snapshot: dict[Path, int] = field(default_factory=dict, init=False, repr=False)
    _reload_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _export_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _export_jobs: dict[str, ExportJob] = field(default_factory=dict, init=False, repr=False)
    _export_executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=2, thread_name_prefix="workspace-export"),
        init=False,
        repr=False,
    )
    _batch_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _batch_jobs: dict[str, BatchJob] = field(default_factory=dict, init=False, repr=False)
    _batch_latest: dict[tuple[str, str | None, str], str] = field(default_factory=dict, init=False, repr=False)
    _batch_declared_files: dict[tuple[str, str], Path] = field(default_factory=dict, init=False, repr=False)
    _batch_executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=4, thread_name_prefix="workspace-batch"),
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = WorkspaceRegistry()
        self._fixed_workspaces = self.registry.list()
        if self.workspace_modules:
            self.reload_workspace_modules(force=True)

    def register_workspace(self, workspace: Any) -> None:
        self.registry.register(workspace)
        self._fixed_workspaces.append(workspace)

    def register_workspace_module(
        self,
        module_name: str,
        attribute: str,
        *,
        watch_path: str | Path | None = None,
    ) -> None:
        """Register a reloadable workspace class, factory, or instance by module path."""
        registration = WorkspaceModuleRegistration(
            module_name,
            attribute,
            Path(watch_path).resolve() if watch_path is not None else None,
        )
        self.workspace_modules = (*self.workspace_modules, registration)
        self.reload_workspace_modules(force=True)

    def reload_browser_profile(self) -> bool:
        """Atomically reload browser.toml and replace its workspace registrations."""
        if self.config_path is None:
            return False
        profile = load_browser_profile(self.config_path)
        registrations = tuple(_profile_registration(spec) for spec in profile.workspaces)
        with self._reload_lock:
            previous = (
                self.registry,
                self.workspace_modules,
                self._workspace_snapshot,
                self.title,
                self.subtitle,
            )
            try:
                self.workspace_modules = registrations
                if registrations:
                    self.reload_workspace_modules(force=True)
                else:
                    replacement = WorkspaceRegistry()
                    for workspace in self._fixed_workspaces:
                        replacement.register(workspace)
                    self.registry = replacement
                    self._workspace_snapshot = {}
                self.title = profile.title or self.title
                self.subtitle = profile.subtitle or self.subtitle
            except Exception:
                (
                    self.registry,
                    self.workspace_modules,
                    self._workspace_snapshot,
                    self.title,
                    self.subtitle,
                ) = previous
                raise
        return True

    def reload_workspace_modules(self, *, force: bool = False) -> bool:
        """Atomically rebuild module-backed workspaces when watched source or data changes."""
        if not self.workspace_modules or (not force and not self.reload_workspaces):
            return False
        with self._reload_lock:
            modules = {
                registration.module_name: importlib.import_module(registration.module_name)
                for registration in self.workspace_modules
            }
            roots = {
                registration.watch_path
                or Path(modules[registration.module_name].__file__).resolve().parent
                for registration in self.workspace_modules
            }
            updated_snapshot = _module_watch_snapshot(roots)
            if not force and updated_snapshot == self._workspace_snapshot:
                return False

            if self._workspace_snapshot:
                importlib.invalidate_caches()
                changed_paths = {
                    path
                    for path in set(self._workspace_snapshot) | set(updated_snapshot)
                    if self._workspace_snapshot.get(path) != updated_snapshot.get(path)
                }
                changed_modules = [
                    module
                    for module in tuple(sys.modules.values())
                    if getattr(module, "__file__", None)
                    and Path(module.__file__).resolve() in changed_paths
                ]
                for module in changed_modules:
                    _reload_module(module)
                for module_name in modules:
                    modules[module_name] = _reload_module(sys.modules[module_name])

            replacement = WorkspaceRegistry()
            for workspace in self._fixed_workspaces:
                replacement.register(workspace)
            for registration in self.workspace_modules:
                target: Any = modules[registration.module_name]
                for component in registration.attribute.split("."):
                    target = getattr(target, component)
                workspace = _instantiate_workspace(target, registration.configuration)
                if registration.metadata_overrides:
                    workspace = _ConfiguredWorkspace(workspace, registration.metadata_overrides)
                replacement.register(workspace)

            self.registry = replacement
            self._workspace_snapshot = updated_snapshot
            return True

    def list_workspaces(self) -> list[dict[str, Any]]:
        return [
            {
                "id": workspace.metadata.identifier,
                "name": workspace.metadata.display_name,
                "description": workspace.metadata.description,
                "category": workspace.metadata.category,
                "tags": list(workspace.metadata.tags),
                "version": workspace.metadata.version,
                "batch": self._batch_capability(workspace, workspace.metadata.identifier),
            }
            for workspace in self.registry.list()
        ]

    def list_items(self, workspace_id: str, query_params: dict[str, list[str]]) -> list[dict[str, Any]]:
        workspace = self.registry.get(workspace_id)
        items = workspace.discover_items()

        query = query_params.get("q", [""])[0]
        tags = set(filter(None, query_params.get("tag", [])))
        sort_by = query_params.get("sort", ["title"])[0]
        descending = query_params.get("desc", ["0"])[0] == "1"
        page = int(query_params.get("page", ["1"])[0])
        page_size = int(query_params.get("page_size", ["50"])[0])

        filtered = filter_items(search_items(items, query), tags=tags)
        sorted_items = sort_items(filtered, by=sort_by, descending=descending)
        paged = paginate_items(sorted_items, page=page, page_size=page_size)

        return [_item_payload(item) for item in paged]

    def browse_items(self, workspace_id: str, query_params: dict[str, list[str]]) -> dict[str, Any]:
        """List immediate files and folders at one source-relative path."""
        workspace = self.registry.get(workspace_id)
        directory = tuple(segment for segment in query_params.get("directory", []) if segment)
        if any(segment in {".", ".."} or "/" in segment or "\\" in segment for segment in directory):
            raise ValueError("Invalid directory path")

        items = workspace.discover_items()
        depth = len(directory)
        descendants = [item for item in items if item.navigation_path[:depth] == directory]
        child_names = sorted(
            {item.navigation_path[depth] for item in descendants if len(item.navigation_path) > depth},
            key=str.casefold,
        )
        immediate = [item for item in descendants if item.navigation_path == directory]

        query = query_params.get("q", [""])[0]
        tags = set(filter(None, query_params.get("tag", [])))
        sort_by = query_params.get("sort", ["title"])[0]
        descending = query_params.get("desc", ["0"])[0] == "1"
        page = int(query_params.get("page", ["1"])[0])
        page_size = int(query_params.get("page_size", ["50"])[0])
        filtered = filter_items(search_items(immediate, query), tags=tags)
        paged = paginate_items(sort_items(filtered, by=sort_by, descending=descending), page=page, page_size=page_size)
        return {
            "path": list(directory),
            "columns": [column.__dict__ for column in workspace.discovery_columns],
            "directories": [
                {"name": name, "path": [*directory, name]}
                for name in child_names
            ],
            "batch": self._batch_capability(workspace, workspace_id),
            "items": [
                {**_item_payload(item), "batch": self._batch_capability(workspace, workspace_id, item.identifier)}
                for item in paged
            ],
        }

    def _batch_capability(
        self,
        workspace: Any,
        workspace_id: str,
        item_id: str | None = None,
    ) -> dict[str, object]:
        capability = getattr(workspace, "batch", None)
        choices = capability.item_actions if capability is not None and item_id is not None else (
            capability.workspace_actions if capability is not None else ()
        )
        actions = []
        with self._batch_lock:
            for choice in choices:
                job_id = self._batch_latest.get((workspace_id, item_id, choice.value))
                status = self.batch_status(job_id) if job_id else self._declared_batch_status(
                    workspace,
                    choice.value,
                    item_id,
                )
                actions.append({**choice.__dict__, **status})
        return {"enabled": bool(actions), "actions": actions}

    @staticmethod
    def _batch_destination(workspace: Any, action: str, item_id: str | None) -> BatchDestination:
        destination = (
            workspace.item_batch_destination(item_id, action)
            if item_id is not None
            else workspace.workspace_batch_destination(action)
        )
        if not isinstance(destination, BatchDestination):
            raise TypeError("Batch destination hooks must return BatchDestination")
        return destination

    def _batch_files(self, job_id: str | None, directory: Path, names: tuple[str, ...] | list[str]) -> list[dict[str, object]]:
        files = []
        for name in names:
            path = (directory / name).resolve()
            encoded_name = quote(name, safe="")
            if job_id:
                url = f"/batches/{job_id}/{encoded_name}"
            else:
                token = uuid5(NAMESPACE_URL, str(path)).hex
                with self._batch_lock:
                    self._batch_declared_files[(token, name)] = path
                url = f"/batch-files/{token}/{encoded_name}"
            files.append({
                "name": name,
                "path": str(path),
                "url": url,
                "open_url": url if path.suffix.lower() in {".html", ".htm"} else None,
            })
        return files

    def _declared_batch_status(self, workspace: Any, action: str, item_id: str | None) -> dict[str, object]:
        destination = self._batch_destination(workspace, action, item_id)
        if destination.directory is None or not destination.files:
            return {"status": "idle"}
        directory = destination.directory.expanduser().resolve()
        if all((directory / name).is_file() for name in destination.files):
            return {
                "status": "ready",
                "summary": destination.summary,
                "files": self._batch_files(None, directory, destination.files),
            }
        return {"status": "idle"}

    def start_batch(self, workspace_id: str, action: str, item_id: str | None = None) -> str:
        """Dispatch one plugin-owned item or workspace batch job."""
        workspace = self.registry.get(workspace_id)
        capability = getattr(workspace, "batch", None)
        choices = capability.item_actions if capability is not None and item_id is not None else (
            capability.workspace_actions if capability is not None else ()
        )
        if action not in {choice.value for choice in choices}:
            raise ValueError("Unsupported batch action")
        key = (workspace_id, item_id, action)
        with self._batch_lock:
            previous_id = self._batch_latest.get(key)
            previous = self._batch_jobs.get(previous_id) if previous_id else None
            if previous is not None and not previous.future.done():
                return previous_id
        destination = self._batch_destination(workspace, action, item_id)
        job_id = uuid4().hex
        temporary = destination.directory is None
        directory = (
            Path(mkdtemp(prefix=f"sigvue-batch-{job_id[:8]}-"))
            if temporary
            else destination.directory.expanduser().resolve()
        )
        directory.mkdir(parents=True, exist_ok=True)

        def build() -> dict[str, object]:
            result = (
                workspace.run_item_batch(item_id, action, directory)
                if item_id is not None
                else workspace.run_workspace_batch(action, directory)
            )
            if not isinstance(result, BatchResult):
                raise TypeError("Batch actions must return BatchResult")
            resolved_directory = directory.resolve()
            files = []
            for value in result.files:
                target = Path(value).resolve()
                if target.parent != resolved_directory or not target.is_file():
                    raise ValueError("Batch results must contain files created in their destination directory")
                if any(character in target.name for character in "\r\n\0"):
                    raise ValueError("Batch result filenames cannot contain control characters")
                files.append(target.name)
            missing_declared = [name for name in destination.files if name not in files]
            if missing_declared:
                raise ValueError(f"Batch result omitted declared files: {', '.join(missing_declared)}")
            return {"files": files, "summary": result.summary}

        future = self._batch_executor.submit(build)
        job = BatchJob(workspace_id, item_id, action, directory, future, temporary)
        with self._batch_lock:
            if previous_id is not None:
                stale = self._batch_jobs.pop(previous_id, None)
                if stale is not None and stale.temporary:
                    shutil.rmtree(stale.directory, ignore_errors=True)
            self._batch_jobs[job_id] = job
            self._batch_latest[key] = job_id
        return job_id

    def batch_status(self, job_id: str) -> dict[str, object]:
        with self._batch_lock:
            job = self._batch_jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        base = {"id": job_id, "action": job.action}
        if not job.future.done():
            return {**base, "status": "running" if job.future.running() else "pending"}
        try:
            result = job.future.result()
        except Exception as exc:
            return {**base, "status": "error", "detail": str(exc)}
        missing = [name for name in result["files"] if not (job.directory / name).is_file()]
        if missing:
            return {
                **base,
                "status": "error",
                "detail": f"Batch output is missing: {', '.join(missing)}",
            }
        return {
            **base,
            "status": "ready",
            "summary": result["summary"],
            "files": self._batch_files(job_id, job.directory, result["files"]),
        }

    def batch_file(self, job_id: str, filename: str) -> Path:
        with self._batch_lock:
            job = self._batch_jobs.get(job_id)
        if job is None or not job.future.done() or job.future.exception() is not None:
            raise KeyError(job_id)
        allowed = set(job.future.result()["files"])
        if filename not in allowed:
            raise KeyError(filename)
        return job.directory / filename

    def declared_batch_file(self, token: str, filename: str) -> Path:
        with self._batch_lock:
            target = self._batch_declared_files.get((token, filename))
        if target is None or not target.is_file():
            raise KeyError(filename)
        return target

    def open_item(self, workspace_id: str, item_id: str, control_values: dict[str, object] | None = None) -> dict[str, Any]:
        request_started = time.perf_counter()
        workspace = self.registry.get(workspace_id)
        requested_values = control_values or {}
        include_static = str(requested_values.get("__include_static_views", "true")).lower() in {"1", "true", "yes", "on"}
        open_with_values = getattr(workspace, "open_item_with_values", None)
        opened = open_with_values(item_id, requested_values) if callable(open_with_values) else workspace.open_item(item_id)
        opened.page.validate()
        values = {control.name: control.default for control in opened.page.controls}
        values.update(requested_values)
        plot_viewports: dict[str, Any] = {}
        try:
            decoded_viewports = json.loads(str(requested_values.get("__plot_viewports", "{}")))
            if isinstance(decoded_viewports, dict):
                plot_viewports = decoded_viewports
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        rendered_views = []
        callbacks_started = time.perf_counter()
        for view in opened.page.views:
            if view.update_policy == "static" and not include_static:
                continue
            value = view.callback(values)
            value = rerasterize_heatmaps(value, plot_viewports.get(view.name))
            rasterized = bool(getattr(value, "_sigvue_rasterized_heatmaps", ()))
            render_kind = detect_render_kind(value)
            kind = render_kind.value
            if render_kind == RenderKind.MATPLOTLIB:
                value = render_matplotlib_figure(value)
            rendered_views.append(
                {
                    "name": view.name,
                    "kind": kind,
                    "value": _json_value(value),
                    "update": view.update_policy,
                    "axis_navigation": view.axis_navigation,
                    "rasterized": rasterized,
                }
            )
        statistics = dict(opened.page.statistics)
        runtime_statistics = {
            label: statistics.pop(label)
            for label in tuple(statistics)
            if label.lower().endswith("runtime") or label.lower().endswith("total")
        }
        runtime_statistics["View callbacks"] = f"{(time.perf_counter() - callbacks_started) * 1_000:.1f} ms"
        annotation = opened.page.annotation
        export = opened.page.export
        annotation_entries = (
            [_annotation_payload(entry) for entry in annotation.discover_callback()]
            if annotation and include_static
            else None
        )
        runtime_statistics["Server total"] = f"{(time.perf_counter() - request_started) * 1_000:.1f} ms"
        return {
            "item": {
                "id": opened.item.identifier,
                "title": opened.item.title,
                "navigation_path": list(opened.item.navigation_path),
            },
            "page": {
                "title": opened.page.title,
                "subtitle": opened.page.subtitle,
                "controls": [control.__dict__ for control in opened.page.controls],
                "control_values": values,
                "playback": {
                    **opened.page.playback.__dict__,
                    "segments": [segment.__dict__ for segment in opened.page.playback.segments],
                },
                "refresh": opened.page.refresh.__dict__,
                "statistics": statistics,
                "runtime_statistics": runtime_statistics,
                "annotation": {
                    "enabled": annotation is not None,
                    "timeline_color_control": annotation.timeline_color_control if annotation else None,
                    "fields": [_annotation_field_payload(field) for field in annotation.fields] if annotation else [],
                    "entries": annotation_entries,
                },
                "export": {
                    "enabled": export is not None,
                    "scopes": [choice.__dict__ for choice in export.scopes] if export else [],
                    "formats": [choice.__dict__ for choice in export.formats] if export else [],
                },
                "views": [view.name for view in opened.page.views],
                "rendered_views": rendered_views,
                "layout": _layout_to_dict(opened.page.layout),
                "metadata": opened.page.metadata,
                "actions": list(opened.page.actions),
            },
        }

    def write_item_annotation(
        self,
        workspace_id: str,
        item_id: str,
        control_values: dict[str, object] | None,
        position_seconds: object,
        duration_seconds: object | None,
        annotation_values: dict[str, object] | None,
    ) -> dict[str, object]:
        """Delegate annotation persistence entirely to the workspace capability."""
        workspace = self.registry.get(workspace_id)
        requested_values = dict(control_values or {})
        open_with_values = getattr(workspace, "open_item_with_values", None)
        opened = open_with_values(item_id, requested_values) if callable(open_with_values) else workspace.open_item(item_id)
        opened.page.validate()
        capability = opened.page.annotation
        if capability is None:
            raise ValueError("This workspace does not provide annotation support")
        try:
            position = float(position_seconds)
            duration = None if duration_seconds is None else float(duration_seconds)
        except (TypeError, ValueError) as error:
            raise ValueError("Annotation position and duration must be numeric") from error
        supplied = {name: str(value) for name, value in (annotation_values or {}).items()}
        for field in capability.fields:
            value = supplied.get(field.name, field.default).strip()
            if field.required and not value:
                raise ValueError(f"{field.label} is required")
            if field.field_type == "select" and value not in {option.value for option in field.options}:
                raise ValueError(f"Invalid value for {field.label}")
            supplied[field.name] = value
        view_selections = {}
        for name, value in requested_values.items():
            if not name.startswith("__view_selection_"):
                continue
            try:
                view_selections[name.removeprefix("__view_selection_")] = int(value)
            except (TypeError, ValueError) as error:
                raise ValueError("Annotation view selections must be non-negative indexes") from error
        result = capability.annotate_callback(
            requested_values,
            AnnotationRequest(
                position_seconds=position,
                duration_seconds=duration,
                values=supplied,
                view_selections=view_selections,
            ),
        )
        return _annotation_payload(result)

    def start_export(
        self,
        workspace_id: str,
        item_id: str,
        control_values: dict[str, object],
        scope: str,
        export_format: str,
    ) -> str:
        """Run a plugin-owned export on the dedicated export executor."""
        job_id = uuid4().hex
        directory = Path(mkdtemp(prefix=f"sigvue-export-{job_id[:8]}-"))

        def build() -> dict[str, object]:
            workspace = self.registry.get(workspace_id)
            requested_values = dict(control_values)
            open_with_values = getattr(workspace, "open_item_with_values", None)
            opened = open_with_values(item_id, requested_values) if callable(open_with_values) else workspace.open_item(item_id)
            capability = opened.page.export
            if capability is None:
                raise ValueError("This workspace does not provide export support")
            if scope not in {choice.value for choice in capability.scopes}:
                raise ValueError("Unsupported export scope")
            if export_format not in {choice.value for choice in capability.formats}:
                raise ValueError("Unsupported export format")
            request = ExportRequest(scope=scope, format=export_format, control_values=requested_values)
            target = Path(capability.export_callback(requested_values, request, directory)).resolve()
            if target.parent != directory.resolve() or not target.is_file():
                raise ValueError("Exporter.export() must return a file created in its destination directory")
            return {"format": export_format, "files": [target.name]}

        future = self._export_executor.submit(build)
        with self._export_lock:
            self._export_jobs[job_id] = ExportJob(directory, future)
        return job_id

    def export_status(self, job_id: str) -> dict[str, object]:
        with self._export_lock:
            job = self._export_jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if not job.future.done():
            return {"id": job_id, "status": "running" if job.future.running() else "pending"}
        try:
            result = job.future.result()
        except Exception as exc:
            return {"id": job_id, "status": "error", "detail": str(exc)}
        files = [
            {"name": name, "url": f"/exports/{job_id}/{name}"}
            for name in result["files"]
        ]
        return {"id": job_id, "status": "ready", "format": result["format"], "files": files}

    def export_file(self, job_id: str, filename: str) -> Path:
        with self._export_lock:
            job = self._export_jobs.get(job_id)
        if job is None or not job.future.done() or job.future.exception() is not None:
            raise KeyError(job_id)
        allowed = set(job.future.result()["files"])
        if filename not in allowed:
            raise KeyError(filename)
        return job.directory / filename

    def finish_export(self, job_id: str) -> None:
        """Remove a completed export after its single output has been sent."""
        with self._export_lock:
            job = self._export_jobs.pop(job_id, None)
        if job is not None:
            shutil.rmtree(job.directory, ignore_errors=True)


def _annotation_payload(annotation: Annotation) -> dict[str, object]:
    if not isinstance(annotation, Annotation):
        raise TypeError("Annotator must return Annotation values")
    return {
        "id": annotation.identifier,
        "position_seconds": annotation.start_seconds,
        "duration_seconds": annotation.duration_seconds,
        "label": annotation.label,
        "comment": annotation.comment,
        "frequency_lower_hz": annotation.frequency_lower_hz,
        "frequency_upper_hz": annotation.frequency_upper_hz,
        "view_selections": dict(annotation.view_selections),
    }


def _annotation_field_payload(field: Any) -> dict[str, object]:
    return {
        **field.__dict__,
        "options": [option.__dict__ for option in field.options],
        "plot_binding": field.plot_binding.__dict__ if field.plot_binding else None,
    }


def _layout_to_dict(layout: Any) -> dict[str, Any]:
    return {
        "kind": layout.kind,
        "view": layout.view,
        "props": layout.props,
        "children": [_layout_to_dict(child) for child in layout.children],
    }


def _json_value(value: Any) -> Any:
    """Return JSON-compatible view data without exposing arbitrary local files."""
    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        return json.loads(to_json())
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _module_watch_snapshot(roots: set[Path]) -> dict[Path, int]:
    suffixes = {".py", ".sigmf-meta", ".sigmf-data"}
    return {
        path: path.stat().st_mtime_ns
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and any(path.name.endswith(suffix) for suffix in suffixes)
    }


def _reload_module(module: Any) -> Any:
    cached = getattr(module, "__cached__", None)
    if cached:
        Path(cached).unlink(missing_ok=True)
    return importlib.reload(module)


def _instantiate_workspace(target: Any, configuration: dict[str, Any]) -> Any:
    if not callable(target):
        return target
    try:
        parameters = inspect.signature(target).parameters
    except (TypeError, ValueError):
        return target()
    if "config" in parameters:
        parameter = parameters["config"]
        if parameter.kind == inspect.Parameter.KEYWORD_ONLY:
            return target(config=dict(configuration))
        return target(dict(configuration))
    required = [
        parameter
        for parameter in parameters.values()
        if parameter.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
        and parameter.default is inspect.Parameter.empty
    ]
    return target(dict(configuration)) if required else target()


def _profile_registration(spec: WorkspaceLaunchSpec) -> WorkspaceModuleRegistration:
    return WorkspaceModuleRegistration(
        spec.module_name,
        spec.attribute,
        spec.watch_path,
        spec.configuration,
        spec.metadata_overrides,
    )


def create_app(
    title: str = "Sigvue",
    *,
    subtitle: str = "Explore scientific and analytical results",
    reload_workspaces: bool = True,
    config_path: str | Path | None = None,
) -> SigvueApp:
    if config_path is not None:
        profile = load_browser_profile(config_path)
        return SigvueApp(
            title=profile.title or title,
            subtitle=profile.subtitle or subtitle,
            reload_workspaces=reload_workspaces,
            workspace_modules=tuple(_profile_registration(spec) for spec in profile.workspaces),
            config_path=Path(config_path).expanduser().resolve(),
        )
    return SigvueApp(
        title=title,
        subtitle=subtitle,
        reload_workspaces=reload_workspaces,
        workspace_modules=(),
    )


def _make_handler(app: SigvueApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _write_html(self, payload: str) -> None:
            data = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _write_javascript(self, payload: str) -> None:
            data = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON request body must be an object")
            return payload

        def _write_export_file(self, path: Path, *, inline: bool = False) -> None:
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            disposition = "inline" if inline else "attachment"
            self.send_header(
                "Content-Disposition",
                f"{disposition}; filename*=UTF-8''{quote(path.name, safe='')}",
            )
            if inline:
                self.send_header("Content-Security-Policy", "sandbox allow-scripts")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with path.open("rb") as stream:
                shutil.copyfileobj(stream, self.wfile)

        # BaseHTTPRequestHandler requires this exact method name.
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/assets/plotly.min.js":
                self._write_javascript(_PLOTLY_JS)
                return
            if parsed.path == "/" or parsed.path.startswith("/workspace/"):
                if app.config_path is not None:
                    app.reload_browser_profile()
                body = _INDEX_HTML.replace("__BROWSER_TITLE__", html_escape(app.title))
                body = body.replace("__BROWSER_SUBTITLE__", html_escape(app.subtitle))
                self._write_html(body)
                return
            if parsed.path == "/health":
                self._write_json(200, {"status": "ok"})
                return
            if parsed.path == "/workspaces":
                try:
                    if parse_qs(parsed.query).get("reload") == ["1"] and app.config_path is not None:
                        app.reload_browser_profile()
                    app.reload_workspace_modules()
                    self._write_json(200, {
                        "workspaces": app.list_workspaces(),
                        "title": app.title,
                        "subtitle": app.subtitle,
                    })
                except Exception as exc:
                    self._write_json(500, {"error": "browser_profile_reload_failed", "detail": str(exc)})
                return

            parts = [unquote(segment) for segment in parsed.path.split("/") if segment]
            try:
                if len(parts) == 2 and parts[0] == "exports":
                    self._write_json(200, app.export_status(parts[1]))
                    return
                if len(parts) == 2 and parts[0] == "batches":
                    self._write_json(200, app.batch_status(parts[1]))
                    return
                if len(parts) == 3 and parts[0] == "batches":
                    batch_path = app.batch_file(parts[1], parts[2])
                    self._write_export_file(
                        batch_path,
                        inline=batch_path.suffix.lower() in {".html", ".htm"},
                    )
                    return
                if len(parts) == 3 and parts[0] == "batch-files":
                    batch_path = app.declared_batch_file(parts[1], parts[2])
                    self._write_export_file(
                        batch_path,
                        inline=batch_path.suffix.lower() in {".html", ".htm"},
                    )
                    return
                if len(parts) == 3 and parts[0] == "exports":
                    export_path = app.export_file(parts[1], parts[2])
                    try:
                        self._write_export_file(export_path)
                    finally:
                        app.finish_export(parts[1])
                    return
                if len(parts) == 3 and parts[0] == "workspaces" and parts[2] == "items":
                    query = parse_qs(parsed.query)
                    self._write_json(200, app.browse_items(parts[1], query))
                    return
                if len(parts) == 4 and parts[0] == "workspaces" and parts[2] == "items":
                    query = {name: values[-1] for name, values in parse_qs(parsed.query).items()}
                    self._write_json(200, app.open_item(parts[1], parts[3], query))
                    return
            except KeyError:
                self._write_json(404, {"error": "workspace_not_found"})
                return
            except ValueError as exc:
                self._write_json(400, {"error": "bad_request", "detail": str(exc)})
                return
            except Exception as exc:  # pragma: no cover
                self._write_json(500, {"error": "internal_error", "detail": str(exc)})
                return

            self._write_json(404, {"error": "not_found"})

        # BaseHTTPRequestHandler requires this exact method name.
        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [unquote(segment) for segment in parsed.path.split("/") if segment]
            try:
                if len(parts) == 5 and parts[0] == "workspaces" and parts[2] == "items" and parts[4] == "annotations":
                    payload = self._read_json()
                    control_values = payload.pop("control_values", {})
                    if not isinstance(control_values, dict):
                        raise ValueError("control_values must be an object")
                    annotation_values = payload.pop("values", {})
                    if not isinstance(annotation_values, dict):
                        raise ValueError("values must be an object")
                    self._write_json(201, app.write_item_annotation(
                        parts[1], parts[3], control_values,
                        payload.get("position_seconds", 0.0), payload.get("duration_seconds"), annotation_values,
                    ))
                    return
                if len(parts) == 5 and parts[0] == "workspaces" and parts[2] == "items" and parts[4] == "exports":
                    payload = self._read_json()
                    control_values = payload.get("control_values", {})
                    if not isinstance(control_values, dict):
                        raise ValueError("control_values must be an object")
                    job_id = app.start_export(
                        parts[1], parts[3], control_values,
                        str(payload.get("scope", "")), str(payload.get("format", "")),
                    )
                    self._write_json(202, {"id": job_id, "status": "pending", "status_url": f"/exports/{job_id}"})
                    return
                if len(parts) == 3 and parts[0] == "workspaces" and parts[2] == "batch":
                    payload = self._read_json()
                    job_id = app.start_batch(parts[1], str(payload.get("action", "")))
                    self._write_json(202, {"id": job_id, "status": "pending", "status_url": f"/batches/{job_id}"})
                    return
                if len(parts) == 5 and parts[0] == "workspaces" and parts[2] == "items" and parts[4] == "batch":
                    payload = self._read_json()
                    job_id = app.start_batch(parts[1], str(payload.get("action", "")), parts[3])
                    self._write_json(202, {"id": job_id, "status": "pending", "status_url": f"/batches/{job_id}"})
                    return
            except KeyError:
                self._write_json(404, {"error": "workspace_not_found"})
                return
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                self._write_json(400, {"error": "bad_request", "detail": str(exc)})
                return
            except Exception as exc:  # pragma: no cover
                self._write_json(500, {"error": "internal_error", "detail": str(exc)})
                return
            self._write_json(404, {"error": "not_found"})

        def log_message(self, message_format: str, *args: Any) -> None:
            return

    return Handler


def _print_batch_catalog(app: SigvueApp) -> None:
    """Print script-friendly batch capabilities and discovered item identifiers."""
    for workspace in app.list_workspaces():
        registered = app.registry.get(workspace["id"])
        if getattr(registered, "batch", None) is None:
            continue
        actions = workspace["batch"]["actions"]
        print(f"{workspace['id']}\t{workspace['name']}")
        for action in actions:
            print(f"  workspace\t{action['value']}\t{action['label']}\t{action['status']}")
        listing = app.browse_items(workspace["id"], {})
        items = list(listing["items"])
        directories = list(listing["directories"])
        while directories:
            directory = directories.pop(0)
            query = {"directory": list(directory["path"])}
            child = app.browse_items(workspace["id"], query)
            items.extend(child["items"])
            directories.extend(child["directories"])
        for item in items:
            for action in item["batch"]["actions"]:
                print(f"  item\t{item['id']}\t{action['value']}\t{action['label']}\t{action['status']}")


def _run_batch_command(app: SigvueApp, args: argparse.Namespace) -> int:
    """Run one catalog batch action synchronously while reporting background status."""
    if args.list_batch:
        _print_batch_catalog(app)
        return 0
    if not args.workspace or not args.action:
        raise ValueError("batch requires --workspace and --action; use --list to inspect choices")
    job_id = app.start_batch(args.workspace, args.action, args.item)
    previous_status = None
    while True:
        status = app.batch_status(job_id)
        if not args.json and status["status"] != previous_status:
            target = f"item {args.item}" if args.item else f"workspace {args.workspace}"
            print(f"{status['status']}: {args.action} on {target}", flush=True)
            previous_status = status["status"]
        if status["status"] not in {"pending", "running"}:
            break
        time.sleep(0.1)
    if status["status"] == "error":
        if args.json:
            print(json.dumps(status))
        else:
            print(f"error: {status.get('detail', 'Batch failed')}", file=sys.stderr)
        return 1
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    saved = []
    for artifact in status.get("files", []):
        destination = output / artifact["name"]
        shutil.copy2(app.batch_file(job_id, artifact["name"]), destination)
        saved.append(str(destination))
    result = {**status, "saved": saved}
    if args.json:
        print(json.dumps(result))
    else:
        print(status.get("summary", "Batch complete"))
        for path in saved:
            print(f"saved: {path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sigvue or dispatch workspace batch actions")
    parser.add_argument("command", nargs="?", choices=("serve", "batch"), default="serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--config", type=Path, help="Load workspace selection and data settings from browser.toml")
    parser.add_argument("--workspace", help="Workspace identifier for a batch action")
    parser.add_argument("--item", help="Optional discovered item identifier for an item batch action")
    parser.add_argument("--action", help="Plugin-defined batch action identifier")
    parser.add_argument("--output", type=Path, default=Path.cwd(), help="Directory for completed batch artifacts")
    parser.add_argument("--list", dest="list_batch", action="store_true", help="List batch-capable workspaces, items, and actions")
    parser.add_argument("--json", action="store_true", help="Print the final batch result as JSON")
    parser.add_argument(
        "--reload",
        dest="reload_workspaces",
        action="store_true",
        default=True,
        help="Reload changed workspace modules when the browser page is refreshed",
    )
    parser.add_argument(
        "--no-reload",
        dest="reload_workspaces",
        action="store_false",
        help="Disable in-process workspace reloading",
    )
    args = parser.parse_args()

    app = create_app(reload_workspaces=args.reload_workspaces, config_path=args.config)
    if args.command == "batch":
        try:
            result = _run_batch_command(app, args)
        except (KeyError, ValueError) as exc:
            parser.error(str(exc))
        raise SystemExit(result)
    server = ThreadingHTTPServer((args.host, args.port), _make_handler(app))
    print(f"Serving {app.title} at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
