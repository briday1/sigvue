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
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from html import escape as html_escape
import importlib
import inspect
import json
import mimetypes
import shutil
import sys
from tempfile import mkdtemp
import time
from traceback import format_exception
from uuid import NAMESPACE_URL, uuid4, uuid5
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, RLock
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from plotly.offline import get_plotlyjs

from sigvue.catalog.browser import filter_items, paginate_items, search_items, sort_items
from sigvue.core.capabilities import Annotation, AnnotationRequest, BatchDestination, BatchResult, ExportRequest
from sigvue.core.layout import selected_view_names
from sigvue.core.models import WorkspaceMetadata
from sigvue.profile import (
    WorkspaceLaunchSpec,
    append_workspace_to_profile,
    load_browser_profile,
    workspace_factory_catalog,
    workspace_launch_spec,
)
from sigvue.registry.registry import WorkspaceRegistry
from sigvue.rendering import render_matplotlib_figure
from sigvue.rendering.dispatch import RenderKind, detect_render_kind


_PLOTLY_JS = get_plotlyjs()
_IMAGE_SUFFIXES = {
    ".avif",
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
}
_INLINE_SUFFIXES = _IMAGE_SUFFIXES | {
    ".css",
    ".htm",
    ".html",
    ".js",
    ".json",
    ".pdf",
    ".txt",
}


_INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__BROWSER_TITLE__</title>
  <style>
    :root { color-scheme: light; --ink:#13212b; --muted:#60717d; --line:#dce5e8; --accent:#087e8b; --wash:#f3f7f7; } html[data-theme="dark"] { color-scheme:dark; --ink:#e7f1f3; --muted:#a9bdc2; --line:#36515b; --accent:#55b9c3; --wash:#193741; } html[data-theme="dark"] body,html[data-theme="dark"] .workspace-sidebar,html[data-theme="dark"] .channel,html[data-theme="dark"] .matplotlib-view,html[data-theme="dark"] .view-switcher-head { background:#10252d } html[data-theme="dark"] .data-toolbar { background:#10252df2 } html[data-theme="dark"] input,html[data-theme="dark"] select,html[data-theme="dark"] .sidebar-toggle,html[data-theme="dark"] .sidebar-close,html[data-theme="dark"] .card,html[data-theme="dark"] .layout-panel,html[data-theme="dark"] .view-choice,html[data-theme="dark"] .view-switcher-select { background:#193741; color:var(--ink); border-color:var(--line) } html[data-theme="dark"] .view-choice.active { background:#164955; color:#e7f1f3; border-color:var(--accent) } html[data-theme="dark"] .card:hover { border-color:var(--accent); box-shadow:none }
    * { box-sizing:border-box } [hidden] { display:none!important } body { margin:0; font:15px/1.5 system-ui,-apple-system,sans-serif; color:var(--ink); background:#fbfcfc }
    header { height:52px; display:flex; align-items:center; gap:8px; padding:0 22px; color:white; background:#102f3a; box-shadow:0 1px 6px #102f3a2b } .header-spacer { min-width:0; flex:1 } header select { min-height:30px; padding:3px 25px 3px 8px; color:#e7f1f3; background:#193741; border-color:#b9d0d54d; font-size:12px } header .sidebar-toggle { min-height:30px; padding:4px 10px; color:#e7f1f3; background:#193741; border-color:#b9d0d54d } header .icon-button { display:grid; width:34px; padding:4px; place-items:center } .icon-button svg { width:18px; height:18px; display:block } .header-nav { display:grid; width:30px; height:30px; padding:0; place-items:center; border:1px solid transparent; border-radius:5px; color:#d8e7ea; background:transparent; cursor:pointer } .header-nav:hover { border-color:#b9d0d54d; background:#ffffff12; color:white } .header-nav:disabled { opacity:.35; cursor:default } .header-nav:disabled:hover { border-color:transparent; background:transparent } .header-nav svg { width:17px; height:17px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round } .fullscreen-toggle { border:1px solid #b9d0d54d; border-radius:5px; padding:3px 8px; background:transparent; color:#e7f1f3; font:18px/1 system-ui,sans-serif; cursor:pointer } .fullscreen-toggle:hover { background:#ffffff1c }
    header b { font-size:16px } header .home-title { all:unset; display:block; min-width:0; flex:0 1 auto; overflow:hidden; cursor:pointer; font:700 16px system-ui,sans-serif; text-overflow:ellipsis; white-space:nowrap } header #app-subtitle { min-width:0; flex:0 1 auto; overflow:hidden; text-overflow:ellipsis; white-space:nowrap } header span { color:#b9d0d5; font-size:13px } header select,header .header-nav,header .workspace-add-toggle,header .notification-center,header .header-menu,header .sidebar-toggle,header .fullscreen-toggle { flex:none }
    main { width:min(1120px,calc(100% - 36px)); margin:34px auto 80px }
    main.item-page,body.hold-item-layout main { width:calc(100% - 24px); max-width:none; margin:12px auto 0 }
    .crumb { color:var(--muted); margin-bottom:20px } .crumb button { all:unset; cursor:pointer; color:var(--accent) }
    h1 { margin:0 0 6px; font-size:30px; letter-spacing:-.02em } .lead { color:var(--muted); margin:0 0 28px }
    .toolbar { display:flex; gap:10px; margin:24px 0 } input,select { min-height:42px; border:1px solid #bdcbd0; border-radius:7px; padding:8px 12px; background:white; font:inherit }
    input[type=search] { flex:1 } button.primary { border:0; border-radius:7px; padding:10px 15px; color:white; background:var(--accent); font:600 14px inherit; cursor:pointer }
    .list { display:flex; flex-direction:column; gap:10px }
    .item-toolbar { display:grid; grid-template-columns:minmax(0,1fr) 132px; align-items:center } .item-toolbar input { min-width:0 } .item-browser-layout { display:grid; grid-template-columns:minmax(0,1fr) 132px; align-items:start; gap:10px } .item-browser { min-width:0; overflow:auto; border:1px solid var(--line); border-radius:8px; background:white } html[data-theme="dark"] .item-browser { background:#10252d } .item-browser table { width:100%; border-collapse:collapse; table-layout:fixed } .item-browser th { padding:8px 12px; color:var(--muted); background:var(--wash); border-bottom:1px solid var(--line); font-size:11px; text-align:left; text-transform:uppercase; letter-spacing:.04em } .item-browser th:first-child { width:28% } .item-browser th:last-child { width:18% } .item-browser th button { all:unset; display:flex; width:100%; gap:5px; align-items:center; cursor:pointer } .item-browser th button:hover { color:var(--accent) } .item-browser td { padding:11px 12px; border-bottom:1px solid var(--line); overflow-wrap:anywhere; vertical-align:middle } .item-browser tbody tr:last-child td { border-bottom:0 } .item-browser .item-row,.item-browser .folder-row { cursor:pointer } .item-browser .item-row:hover,.item-browser .folder-row:hover { background:color-mix(in srgb,var(--accent) 7%,transparent) } .item-name { display:flex; flex-direction:column; gap:2px } .item-name small { color:var(--muted) } .item-tags { display:flex; flex-wrap:wrap; gap:3px } .item-tags .tag { margin:0 } .discovery-null { color:var(--muted) } .item-action-rail { width:132px } .item-action-rail-head { border-bottom:1px solid transparent } .item-action-row { display:flex; align-items:center; justify-content:flex-end } .item-action-row.folder-spacer { pointer-events:none }
    .result-browser { display:grid; grid-template-columns:minmax(250px,34%) minmax(0,1fr); min-height:min(720px,calc(100vh - 230px)); overflow:hidden; border:1px solid var(--line); border-radius:9px; background:white } html[data-theme="dark"] .result-browser { background:#10252d } .result-browser-list { min-width:0; overflow:auto; border-right:1px solid var(--line) } .result-entry { display:grid; width:100%; grid-template-columns:24px minmax(0,1fr) auto; align-items:center; gap:7px; min-height:43px; padding:7px 10px; border:0; border-bottom:1px solid var(--line); color:var(--ink); background:transparent; text-align:left; cursor:pointer } .result-entry:hover,.result-entry.active { background:color-mix(in srgb,var(--accent) 8%,transparent) } .result-entry.active { box-shadow:inset 3px 0 var(--accent) } .result-entry-icon { color:var(--muted); text-align:center } .result-entry-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap } .result-entry-size { color:var(--muted); font:11px ui-monospace,monospace; white-space:nowrap } .result-preview { position:relative; display:flex; min-width:0; min-height:420px; flex-direction:column; align-items:stretch; background:color-mix(in srgb,var(--wash) 72%,transparent) } .result-preview-toolbar { display:flex; min-height:43px; align-items:center; gap:7px; padding:6px 9px; border-bottom:1px solid var(--line) } .result-preview-toolbar strong { min-width:0; flex:1; overflow:hidden; font-size:12px; text-overflow:ellipsis; white-space:nowrap } .result-preview-toolbar button,.result-preview-toolbar a,.result-file-actions a { min-height:29px; padding:4px 9px; border:1px solid var(--line); border-radius:5px; color:var(--ink); background:var(--wash); font:600 11px system-ui,sans-serif; text-decoration:none; cursor:pointer } .result-preview-toolbar button:hover,.result-preview-toolbar a:hover,.result-file-actions a:hover { border-color:var(--accent); color:var(--accent) } .result-image-stage { display:grid; min-height:0; flex:1; overflow:auto; padding:14px; place-items:center } .result-image-stage img { display:block; max-width:100%; max-height:calc(100vh - 255px); object-fit:contain; image-rendering:auto; box-shadow:0 3px 18px #071b2228 } .result-empty-preview,.result-file-preview { display:grid; min-height:0; flex:1; padding:28px; place-content:center; color:var(--muted); text-align:center } .result-file-preview strong { color:var(--ink); font-size:17px } .result-file-preview p { max-width:560px; overflow-wrap:anywhere } .result-file-actions { display:flex; flex-wrap:wrap; justify-content:center; gap:8px } @media(max-width:760px){.result-browser { grid-template-columns:1fr; min-height:0 }.result-browser-list { max-height:300px; border-right:0; border-bottom:1px solid var(--line) }.result-preview { min-height:420px }}
    .result-browser { height:624px; min-height:384px } .result-browser-list { min-height:0; overscroll-behavior:contain; scrollbar-gutter:stable } .result-entry { padding-left:calc(10px + var(--result-indent,0px)) } .result-entry-icon { display:grid; width:24px; height:24px; place-items:center } .result-disclosure { display:block; font-size:19px; line-height:1; transition:transform .12s ease } .result-entry[aria-expanded="true"] .result-disclosure { transform:rotate(90deg) } .result-entry.loading .result-disclosure { animation:result-disclosure-pulse .7s ease-in-out infinite alternate } .result-tree-message { min-height:34px; padding:7px 10px 7px calc(41px + var(--result-indent,0px)); color:var(--muted); border-bottom:1px solid var(--line); font-size:12px } .result-tree-message.error { color:#b42318 } .result-preview { min-height:0; overflow:hidden } .result-preview-toolbar { flex:none } .result-image-stage { display:flex; min-width:0; min-height:0; flex:1 1 0; align-items:center; justify-content:center; overflow:hidden } .result-image-stage img { width:auto; height:auto; max-width:100%; max-height:100%; flex:none } @keyframes result-disclosure-pulse { to { opacity:.35 } } @media(max-width:760px){.result-browser { grid-template-rows:minmax(140px,32%) minmax(0,1fr); height:624px; min-height:384px }.result-browser-list { max-height:none }.result-preview { min-height:0 }}
    .card { display:grid; grid-template-columns:minmax(180px,1fr) 2fr auto auto; align-items:center; gap:18px; border:1px solid var(--line); border-radius:8px; background:white; padding:16px 18px; box-shadow:0 2px 8px #17323c0b; cursor:pointer; transition:.15s }
    .card:hover { border-color:#8eb9bf; box-shadow:0 4px 14px #17323c14 } .card:not(:has(.batch-menu)) { grid-template-columns:minmax(180px,1fr) 2fr auto } .card h2 { font-size:17px; margin:4px 0 } .card p { margin:0 } .card-tags { text-align:right; min-width:130px }
    .muted { color:var(--muted) } .tag { display:inline-block; border-radius:999px; padding:3px 9px; margin:2px 4px 2px 0; font-size:12px; background:#e8f3f3; color:#17626a }
    .batch-controls { display:flex; width:132px; align-items:center; justify-content:flex-end; gap:6px } .batch-menu { position:relative; z-index:5 } .batch-menu summary { position:relative; display:grid; width:34px; height:34px; padding:0; place-items:center; border:1px solid var(--line); border-radius:50%; color:var(--accent); background:var(--wash); cursor:pointer; list-style:none } .batch-menu summary::-webkit-details-marker { display:none } .batch-menu[open] summary { border-color:var(--accent); box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 14%,transparent) } .batch-folder { display:flex; width:92px; height:34px; align-items:center; justify-content:center; gap:6px; border:1px solid var(--line); border-radius:7px; color:var(--accent); background:var(--wash); font:650 11px system-ui,sans-serif; cursor:pointer; text-decoration:none } .batch-folder:hover { border-color:var(--accent); background:color-mix(in srgb,var(--accent) 8%,var(--wash)); box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 10%,transparent) } .batch-folder[aria-disabled="true"] { color:var(--muted); opacity:.38; pointer-events:none } .batch-folder svg { width:17px; height:17px; flex:none; fill:none; stroke:currentColor; stroke-width:1.7; stroke-linecap:round; stroke-linejoin:round } .batch-folder-label { line-height:1 } .batch-play { margin-left:2px; font-size:15px; line-height:1 } .batch-menu-popover { position:absolute; z-index:30; top:40px; right:0; width:320px; padding:8px; border:1px solid var(--line); border-radius:8px; background:#fbfcfc; box-shadow:0 12px 28px #102f3a30 } html[data-theme="dark"] .batch-menu-popover { background:#193741 } .batch-action-row+.batch-action-row { border-top:1px solid var(--line) } .batch-action { display:grid; width:100%; grid-template-columns:1fr auto; gap:8px; padding:9px; border:0; border-radius:6px; color:var(--ink); background:transparent; text-align:left; cursor:pointer } .batch-action:hover { background:var(--wash) } .batch-artifacts { display:grid; gap:5px; padding:0 9px 8px; font-size:12px } .batch-artifact { display:flex; min-width:0; align-items:center; gap:6px } .batch-path { min-width:0; flex:1; overflow:hidden; color:var(--muted); text-overflow:ellipsis; white-space:nowrap } .batch-open { color:var(--accent) } .copy-path { flex:none; padding:3px 7px; border:1px solid var(--line); border-radius:5px; color:var(--ink); background:var(--wash); font:600 11px system-ui,sans-serif; cursor:pointer } .copy-path:hover { border-color:var(--accent); color:var(--accent) } .batch-state { color:var(--muted); font-size:12px } .batch-state.running,.batch-state.pending { color:#b7791f } .batch-state.ready { color:#16803c } .batch-state.error { color:#b42318 } .item-browser th.tags-column { width:18% }
    .notification-center { position:relative } .notification-center>summary { display:flex; min-width:34px; min-height:30px; align-items:center; justify-content:center; gap:5px; padding:4px 7px; border:1px solid #b9d0d54d; border-radius:6px; color:#e7f1f3; background:#193741; cursor:pointer; list-style:none } .notification-center>summary::-webkit-details-marker { display:none } .notification-center>summary svg { width:18px; height:18px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round } .notification-center[open]>summary { border-color:#8ed0d7 } .notification-badge { display:grid; min-width:18px; height:18px; padding:0 5px; place-items:center; border-radius:999px; color:#102f3a; background:#8ed0d7; font-size:10px } .notification-popover { position:absolute; z-index:80; top:38px; right:0; width:min(440px,calc(100vw - 24px)); overflow:hidden; border:1px solid var(--line); border-radius:9px; color:var(--ink); background:#fbfcfc; box-shadow:0 14px 32px #071b2240 } html[data-theme="dark"] .notification-popover { background:#193741 } .notification-head { display:flex; align-items:center; justify-content:space-between; padding:11px 13px; border-bottom:1px solid var(--line) } .notification-head strong { font-size:13px } #notification-list { max-height:min(330px,calc(100vh - 120px)); overflow-y:auto; overscroll-behavior:contain } .notification-empty { margin:0; padding:18px; color:var(--muted); font-size:12px; text-align:center } .notification-item { padding:12px 13px; border-bottom:1px solid var(--line) } .notification-item:last-child { border-bottom:0 } .notification-title { display:flex; align-items:start; gap:8px } .notification-title strong { flex:1; color:var(--ink); font-size:13px } .notification-status { flex:none; font-size:11px; font-weight:700; text-transform:uppercase } .notification-status.pending,.notification-status.running,.notification-status.cancelling { color:#b7791f } .notification-status.ready { color:#16803c } .notification-status.error { color:#b42318 } .notification-status.cancelled { color:var(--muted) } .notification-dismiss { flex:none; padding:0 4px; border:0; color:var(--muted); background:transparent; font-size:18px; line-height:18px; cursor:pointer } .notification-summary,.notification-context { margin:4px 0 0; color:var(--muted); font-size:12px } .notification-context { font-size:11px } .notification-files { display:grid; gap:6px; margin-top:9px }
    .notification-progress { margin-top:9px } .notification-progress-errors { display:grid; gap:4px; max-height:112px; overflow-y:auto; padding-right:3px } .notification-progress-item { padding:4px 6px; border-radius:5px; color:#b42318; background:color-mix(in srgb,#b42318 7%,var(--wash)); font-size:11px } .notification-progress-row { display:flex; min-width:0; align-items:center; gap:7px } .notification-progress-state { width:42px; flex:none; color:#b42318; font-size:10px; font-weight:750; text-transform:uppercase } .notification-progress-name { min-width:0; overflow:hidden; flex:1; text-overflow:ellipsis; white-space:nowrap } .notification-progress-log { margin:4px 0 0 49px; color:#b42318 } .notification-progress-log summary { cursor:pointer; font-size:10px } .notification-progress-log pre { max-height:130px; margin:5px 0 0; overflow:auto; padding:6px; border:1px solid color-mix(in srgb,#b42318 30%,var(--line)); border-radius:4px; color:var(--ink); background:var(--wash); font:10px/1.35 ui-monospace,monospace; white-space:pre-wrap; user-select:text } .notification-progress-head { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:7px; color:var(--muted); font-size:10px } .notification-cancel { min-height:25px; padding:3px 8px; border:1px solid var(--line); border-radius:5px; color:#b42318; background:var(--wash); font:650 10px system-ui,sans-serif; cursor:pointer } .notification-cancel:disabled { color:var(--muted); cursor:wait } .notification-progress-track { display:flex; height:5px; margin-top:5px; overflow:hidden; border-radius:999px; background:var(--line) } .notification-progress-success { height:100%; background:#20a957; transition:width .2s ease } .notification-progress-failed { height:100%; background:#d13c32; transition:width .2s ease }
    .notification-toasts { position:fixed; z-index:90; top:60px; right:12px; display:flex; width:min(360px,calc(100vw - 24px)); flex-direction:column; gap:8px; pointer-events:none } .notification-toast { padding:10px 12px; border:1px solid color-mix(in srgb,#16803c 45%,var(--line)); border-radius:8px; color:var(--ink); background:color-mix(in srgb,#16803c 8%,#fbfcfc); box-shadow:0 10px 26px #071b2238; animation:notification-toast-life 3s ease forwards } .notification-toast.error { border-color:color-mix(in srgb,#b42318 45%,var(--line)); background:color-mix(in srgb,#b42318 8%,#fbfcfc) } html[data-theme="dark"] .notification-toast { background:color-mix(in srgb,#16803c 12%,#193741) } html[data-theme="dark"] .notification-toast.error { background:color-mix(in srgb,#b42318 12%,#193741) } .notification-toast strong { display:block; font-size:13px } .notification-toast span { display:block; margin-top:2px; color:var(--muted); font-size:12px } @keyframes notification-toast-life { 0% { opacity:0; transform:translateY(-6px) } 8%,78% { opacity:1; transform:translateY(0) } 100% { opacity:0; transform:translateY(-4px) } }
    .data-toolbar { position:sticky; top:0; z-index:20; display:flex; align-items:center; gap:10px; min-height:46px; margin:0 -12px 4px; padding:6px 16px; background:#fbfcfcf2; border-bottom:1px solid var(--line); backdrop-filter:blur(8px) } .data-toolbar-spacer { flex:1 }
    .playback-bar { display:flex; align-items:center; gap:10px; flex:1; min-width:240px } .playback-bar .primary { padding:6px 10px; min-width:72px } .playback-track { position:relative; display:flex; flex:1; align-items:center; min-width:80px } .playback-track input[type=range] { position:relative; z-index:2; width:100%; min-height:0; padding:0 } .annotation-markers { position:absolute; z-index:0; inset:0 8px; pointer-events:none } .annotation-marker { position:absolute; top:0; bottom:0; width:1px; margin:0; padding:0; border:0; border-radius:0; background:var(--annotation-marker-color,#ffffff); box-shadow:none; opacity:.35; pointer-events:none } .annotation-marker.clustered { width:1px; margin:0; border:0; opacity:.55 } .playback-bar #current-time { flex:none; width:98px; min-height:30px; padding:4px 7px; text-align:right; font:12px ui-monospace,monospace } .playback-bar #counter { width:82px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap }
    .windowed-bar { display:flex; align-items:center; gap:8px; width:100%; min-width:0 } .windowed-track-stack { flex:1; height:30px; min-width:120px } .windowed-label { position:absolute; z-index:4; top:2px; left:6px; max-width:calc(100% - 42px); overflow:hidden; color:var(--muted); font-size:9px; font-weight:600; line-height:10px; text-overflow:ellipsis; text-shadow:0 0 3px var(--wash),0 0 3px var(--wash); white-space:nowrap; pointer-events:none } .windowed-bar .windowed-time,.windowed-bar .windowed-width { flex:none; width:88px; height:30px; min-height:30px; padding:4px 7px; text-align:right; font:12px ui-monospace,monospace } .windowed-total { flex:none; width:82px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap } .windowed-width-label { display:flex; flex:none; min-width:0; align-items:center; gap:7px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap } .windowed-bar .windowed-width { width:118px } .windowed-track { position:relative; width:100%; height:30px; overflow:hidden; border:1px solid var(--line); border-radius:5px; background:var(--wash); touch-action:none } .windowed-overview { position:absolute; z-index:1; inset:2px; width:calc(100% - 4px); height:calc(100% - 4px); pointer-events:none } .windowed-full-extent { position:absolute; z-index:5; top:4px; right:11px; width:20px; height:20px; min-height:20px; padding:0; border:1px solid color-mix(in srgb,var(--line) 70%,transparent); border-radius:4px; background:color-mix(in srgb,var(--wash) 78%,transparent); color:var(--ink); font:14px/18px system-ui,sans-serif } .windowed-full-extent:hover { border-color:var(--accent); color:var(--accent) } .windowed-selection { position:absolute; z-index:2; top:0; bottom:0; margin:0; padding:0; border:0; border-radius:0; background:color-mix(in srgb,var(--accent) 22%,transparent); cursor:grab } .windowed-selection:active { cursor:grabbing } .windowed-handle { position:absolute; z-index:3; top:0; bottom:0; width:9px; margin-left:-4px; padding:0; border:0; border-left:2px solid var(--accent); border-right:2px solid var(--accent); border-radius:1px; background:color-mix(in srgb,var(--accent) 45%,transparent); cursor:ew-resize } .windowed-selection:focus-visible,.windowed-handle:focus-visible { outline:2px solid var(--accent); outline-offset:-2px }
    .segmented-bar { display:flex; align-items:center; gap:8px; width:100%; min-width:0 } .segment-actions,.segment-playback { display:flex; flex:none; align-items:center; gap:6px } .segment-actions button,.segment-playback button { min-height:30px; padding:4px 10px; border:1px solid var(--line); border-radius:6px; color:var(--ink); background:var(--wash); font:600 12px inherit; cursor:pointer } .segment-actions button:hover:not(:disabled),.segment-actions button:focus-visible,.segment-playback button:hover:not(:disabled),.segment-playback button:focus-visible { border-color:var(--accent); color:var(--accent); background:color-mix(in srgb,var(--accent) 10%,var(--wash)) } .segment-actions button:disabled,.segment-playback button:disabled { opacity:.42; cursor:default } .segment-number { display:flex; align-items:center; gap:3px; color:var(--muted); font:11px ui-monospace,monospace; white-space:nowrap } .segment-number input { width:54px; min-height:30px; padding:4px 5px; text-align:right; font:12px ui-monospace,monospace } .segment-number.segment-step input { width:44px } .segmented-track { position:relative; flex:1; height:34px; min-width:120px; border:1px solid var(--line); border-radius:5px; background:var(--wash); cursor:ew-resize; touch-action:none; user-select:none } .segmented-track.scrubbing { cursor:grabbing } .segmented-track::before { position:absolute; top:50%; right:8px; left:8px; height:1px; background:var(--line); content:"" } .segment-marker-layer { position:absolute; inset:0 8px } .segment-marker { position:absolute; z-index:1; top:50%; width:12px; height:12px; margin:-6px 0 0 -6px; padding:0; border:2px solid var(--wash); border-radius:50%; background:var(--muted); box-shadow:0 0 0 1px var(--line); cursor:pointer; transform:scale(.85); transition:transform .12s,background .12s } .segment-marker:hover,.segment-marker:focus-visible { z-index:2; outline:2px solid var(--accent); outline-offset:2px; transform:scale(1.15) } .segment-marker.active { background:var(--accent); box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 35%,transparent); transform:scale(1.25) } .segment-count { flex:none; width:72px; color:var(--muted); font:12px ui-monospace,monospace; text-align:right; white-space:nowrap } .segment-time { flex:none; width:214px; overflow:hidden; color:var(--muted); font:12px ui-monospace,monospace; font-variant-numeric:tabular-nums; text-align:right; text-overflow:ellipsis; white-space:nowrap }
    .sidebar-toggle,.sidebar-close { border:1px solid var(--line); border-radius:6px; padding:5px 10px; background:white; color:var(--muted); font:600 12px inherit; cursor:pointer } .sidebar-toggle.has-view-parameters { color:var(--accent); border-color:var(--accent) } .workspace-sidebar { position:fixed; z-index:40; top:52px; right:0; bottom:0; display:flex; flex-direction:column; width:min(420px,calc(100vw - 20px)); padding:18px; overflow-y:auto; overflow-x:hidden; background:#fbfcfc; border-left:1px solid var(--line); box-shadow:-10px 0 30px #17323c1c; transform:translateX(102%); transition:transform .18s ease } .workspace-sidebar * { min-width:0 } .workspace-sidebar .table-wrap { overflow:visible; padding:8px 0 } .workspace-sidebar .data-table th,.workspace-sidebar .data-table td { white-space:normal; overflow-wrap:anywhere } .workspace-sidebar.open { transform:translateX(0) } .sidebar-backdrop { position:fixed; z-index:35; inset:52px 0 0; border:0; background:#102f3a24; opacity:0; pointer-events:none; transition:opacity .18s ease } .sidebar-backdrop.open { opacity:1; pointer-events:auto } .sidebar-head { display:flex; align-items:start; gap:12px; padding-bottom:16px; border-bottom:1px solid var(--line) } .sidebar-head .crumb { margin:0 0 7px; font-size:12px } .sidebar-title { min-width:0; flex:1 } .sidebar-title h1 { margin:0; font-size:20px; line-height:1.25 } .sidebar-title .subtitle { display:block; margin-top:4px; color:var(--muted); font-size:13px } .sidebar-close { flex:none; padding:4px 8px } .analysis-panel { display:flex; flex-direction:column; gap:12px; padding-top:16px } .analysis-panel h2 { margin:0; font-size:13px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em } .settings-group { overflow:hidden; border:1px solid var(--line); border-radius:8px; background:white } html[data-theme="dark"] .settings-group { background:#193741 } .settings-group>summary { display:flex; align-items:center; padding:10px 12px; color:var(--ink); cursor:pointer; font-size:12px; font-weight:700; list-style:none } .settings-group>summary::-webkit-details-marker { display:none } .settings-group>summary::after { content:'⌄'; margin-left:auto; color:var(--muted); font-size:16px; transition:transform .15s } .settings-group[open]>summary::after { transform:rotate(180deg) } .settings-group-body { padding:11px 12px 12px; border-top:1px solid var(--line); background:var(--wash) } .view-settings-empty { margin:8px 0 0; color:var(--muted); font-size:12px } .control-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px } .control-fields:empty { display:none } .control-fields label { display:flex; flex-direction:column; gap:3px; color:var(--muted); font-size:11px } .control-fields select,.control-fields input { min-height:34px; padding:5px 8px; color:var(--ink) } .control-fields select { padding-right:26px } .control-fields input[type=number] { width:100% } .control-fields input[type=color] { width:100%; padding:3px; cursor:pointer } .view-stats { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:7px 12px; margin:0; font-size:12px } .view-stats div { display:contents } .view-stats dt { color:var(--muted) } .view-stats dd { margin:0; text-align:right; color:var(--ink); font:12px ui-monospace,monospace; overflow-wrap:anywhere; white-space:normal } .view-stats .runtime-total dt,.view-stats .runtime-total dd { margin-top:4px; padding-top:7px; border-top:1px solid var(--line); color:var(--ink); font-weight:700 }
    .header-menu { position:relative } .header-menu > summary { min-height:30px; padding:4px 10px; border:1px solid #b9d0d54d; border-radius:6px; color:#e7f1f3; background:#193741; font:600 12px/20px system-ui,sans-serif; cursor:pointer; list-style:none } .header-menu > summary::-webkit-details-marker { display:none } .header-menu[open] > summary { border-color:var(--accent) } .header-popover { position:absolute; z-index:60; top:38px; right:0; display:flex; flex-direction:column; gap:10px; width:280px; padding:14px; border:1px solid var(--line); border-radius:8px; color:var(--ink); background:#fbfcfc; box-shadow:0 12px 30px #102f3a33 } html[data-theme="dark"] .header-popover { background:#193741 } .header-popover label { display:flex; flex-direction:column; gap:3px; color:var(--muted); font-size:11px } .header-popover input,.header-popover select,.header-popover textarea { width:100%; min-height:34px; padding:5px 8px; color:var(--ink); background:white; border:1px solid var(--line); border-radius:5px; font:inherit } html[data-theme="dark"] .header-popover input,html[data-theme="dark"] .header-popover select,html[data-theme="dark"] .header-popover textarea { background:#10252d } .header-popover textarea { min-height:70px; resize:vertical } .header-popover .primary { align-self:flex-end; min-height:34px; padding:6px 12px }
    .style-picker-list { display:flex; flex-direction:column; gap:7px; margin-top:9px } .style-picker { overflow:hidden; border:1px solid var(--line); border-radius:8px; background:white } html[data-theme="dark"] .style-picker { background:#193741 } .style-picker summary { display:flex; align-items:center; gap:9px; min-height:40px; padding:7px 10px; cursor:pointer; list-style:none; user-select:none } .style-picker summary::-webkit-details-marker { display:none } .style-picker summary::after { content:'⌄'; margin-left:auto; color:var(--muted); font-size:16px; transition:transform .15s } .style-picker[open] summary::after { transform:rotate(180deg) } .style-swatch { flex:none; width:18px; height:18px; border:2px solid #ffffffcc; border-radius:50%; box-shadow:0 0 0 1px #13212b2e } .style-picker-name { min-width:0; overflow:hidden; color:var(--ink); font-size:13px; font-weight:650; text-overflow:ellipsis; white-space:nowrap } .style-picker-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px 12px; padding:10px; border-top:1px solid var(--line); background:var(--wash) } .style-picker-fields label { display:flex; flex-direction:column; gap:3px; color:var(--muted); font-size:11px } .style-picker-fields input,.style-picker-fields select { width:100%; min-height:34px; padding:5px 8px; color:var(--ink) } .style-picker-fields input[type=color] { padding:3px; cursor:pointer }
    .colormap-picker { overflow:hidden; border:1px solid var(--line); border-radius:8px; background:white } html[data-theme="dark"] .colormap-picker { background:#193741 } .colormap-picker summary { display:grid; grid-template-columns:minmax(90px,1fr) auto auto; align-items:center; gap:9px; min-height:44px; padding:7px 10px; cursor:pointer; list-style:none; user-select:none } .colormap-picker summary::-webkit-details-marker { display:none } .colormap-picker summary::after { content:'⌄'; color:var(--muted); font-size:16px; transition:transform .15s } .colormap-picker[open] summary::after { transform:rotate(180deg) } .colormap-preview { display:block; height:17px; min-width:90px; border:1px solid #13212b2e; border-radius:4px } .colormap-picker-name { overflow:hidden; color:var(--ink); font-size:12px; font-weight:650; text-overflow:ellipsis; white-space:nowrap } .colormap-options { display:flex; flex-direction:column; gap:3px; max-height:280px; overflow:auto; padding:7px; border-top:1px solid var(--line); background:var(--wash) } .colormap-option { display:grid; grid-template-columns:minmax(100px,1fr) 76px; align-items:center; gap:10px; min-height:34px; padding:5px 7px; border:1px solid transparent; border-radius:5px; color:var(--ink); background:transparent; text-align:left; cursor:pointer } .colormap-option:hover,.colormap-option.selected { border-color:var(--accent); background:color-mix(in srgb,var(--accent) 10%,transparent) } .colormap-option .colormap-preview { width:100% }
    .limits-picker { padding:10px; border:1px solid var(--line); border-radius:8px; background:white } html[data-theme="dark"] .limits-picker { background:#193741 } .limits-picker-head { display:grid; grid-template-columns:minmax(0,1fr) 84px 12px 84px; align-items:center; gap:6px; color:var(--muted); font-size:12px } .limits-picker-name { overflow:hidden; color:var(--ink); font-weight:650; text-overflow:ellipsis; white-space:nowrap } .limits-picker-head input { width:100%; min-height:32px; padding:4px 6px; text-align:right; color:var(--ink); font:12px ui-monospace,monospace } .limits-separator { text-align:center }
    .toggle-control { position:relative; display:inline-flex; width:38px; height:22px; align-items:center; cursor:pointer } .toggle-control input { position:absolute; width:1px!important; height:1px; min-height:0!important; margin:0; padding:0!important; opacity:0 } .toggle-track { width:38px; height:22px; border:1px solid var(--line); border-radius:999px; background:var(--wash); transition:.15s } .toggle-track::after { display:block; width:16px; height:16px; margin:2px; border-radius:50%; background:var(--muted); content:""; transition:.15s } .toggle-control input:checked + .toggle-track { border-color:var(--accent); background:color-mix(in srgb,var(--accent) 25%,var(--wash)) } .toggle-control input:checked + .toggle-track::after { background:var(--accent); transform:translateX(16px) } .toggle-control input:focus-visible + .toggle-track { outline:2px solid var(--accent); outline-offset:2px }
    .data-stage { height:400px; min-height:0; overflow:hidden } #active-view,.view,.view-slot { height:100%; min-height:0 } .view-slot:empty::before { display:grid; height:100%; place-items:center; color:var(--muted); content:"Loading view…"; font-size:13px } .layout-tabs { position:relative; display:flex; flex-direction:column; height:100%; min-height:0 } .layout-tab-panes { position:relative; flex:1; min-height:0 } .tabs { flex:none; display:flex; gap:4px; overflow-x:auto; border-bottom:1px solid var(--line); margin:0 0 4px; padding-left:4px } .tab { flex:none; border:0; border-bottom:3px solid transparent; background:none; padding:9px 13px 7px; color:var(--muted); font:600 13px inherit; cursor:pointer } .tab.active { color:var(--accent); border-color:var(--accent) } .layout-tab-pane { width:100%; height:100%; min-height:0 } .layout-tab-pane:not(.active) { position:absolute; inset:0; visibility:hidden; pointer-events:none }
    .view h1 { font-size:20px } .view h2 { font-size:16px } .plotly-view { width:100%; height:100%; min-height:0 } .matplotlib-view { display:block; width:100%; height:100%; min-height:0; object-fit:contain; background:white } .playback-grid { display:grid; width:100%; height:100%; min-height:0; grid-template-columns:var(--grid-template,repeat(2,minmax(0,1fr))); grid-template-rows:var(--grid-rows,minmax(0,1fr)); gap:4px } .playback-grid.single-plot { display:block; height:100% } .single-plot .channel { width:100%; height:100%; border:0 } .view-switcher { position:relative; display:flex; flex-direction:column; height:100%; min-height:0 } .view-pane { width:100%; flex:1; min-height:0 } .view-pane:not(.active) { position:absolute; inset:40px 0 0; visibility:hidden; pointer-events:none } .view-switcher-head { position:relative; z-index:2; flex:none; height:40px; display:flex; align-items:center; gap:10px; padding:4px 8px; border-bottom:1px solid var(--line); background:white } .view-switcher-label { color:var(--muted); font-size:12px; font-weight:600 } .view-choice { border:1px solid var(--line); border-radius:5px; background:white; padding:4px 9px; color:var(--muted); font:12px inherit; cursor:pointer } .view-choice.active { border-color:var(--accent); background:#e8f3f3; color:#17626a } .view-switcher-select { min-height:30px; min-width:150px; padding:4px 28px 4px 8px; border:1px solid var(--line); border-radius:5px; background:white; color:var(--ink); font:12px inherit } .parameter-group { flex:none; display:grid; grid-template-columns:repeat(var(--parameter-columns,1),minmax(0,1fr)); gap:9px 12px; padding:10px 12px; border:1px solid var(--line); border-radius:7px; background:var(--wash) } .parameter-group-title { grid-column:1/-1; color:var(--muted); font-size:11px; font-weight:700; letter-spacing:.04em; text-transform:uppercase } .parameter-control { display:flex; flex-direction:column; gap:3px; min-width:0; color:var(--muted); font-size:11px } .parameter-control input,.parameter-control select { width:100%; min-height:34px; padding:5px 8px; color:var(--ink) } .channel { min-width:0; min-height:0; height:100%; overflow:hidden; border-right:1px solid var(--line); border-bottom:1px solid var(--line); background:white } .channel:nth-child(2n) { border-right:0 } .layout-column { display:flex; flex-direction:column; gap:8px; height:100%; min-height:0; overflow:auto } .layout-column > .plotly-view,.layout-column > .matplotlib-view { flex:1 } .layout-row { display:flex; gap:8px; height:100%; min-height:0 } .layout-panel { height:100%; min-height:0; overflow:auto; padding:12px; border:1px solid var(--line); border-radius:7px } .prose,.text-view { padding:16px; color:var(--ink) } .prose h1,.prose h2,.prose h3 { margin:0 0 8px } .table-wrap { width:100%; height:100%; min-height:0; max-height:100%; overflow:auto; overscroll-behavior:contain; padding:8px } .workspace-sidebar .table-wrap { height:auto; max-height:none } .data-table { width:100%; border-collapse:collapse; font-size:12px } .data-table th { position:sticky; top:0; z-index:1; background:var(--wash); color:var(--muted); text-align:left } .data-table th,.data-table td { padding:7px 9px; border-bottom:1px solid var(--line); white-space:nowrap } .empty,.error { padding:36px; text-align:center; color:var(--muted); border:1px dashed #bac9cd; border-radius:10px }
    [data-render-view] { width:100%; height:100%; min-width:0; min-height:0; overflow:auto; overscroll-behavior:contain; scrollbar-gutter:stable }
    .error { color:#8c2e2e; background:#fff7f7 } @media(max-width:700px){header{padding:0 14px}header span{display:none}main{margin-top:20px}main.item-page{width:calc(100% - 12px);margin-top:6px}.toolbar{flex-wrap:wrap}.playback-grid{grid-template-columns:1fr;grid-template-rows:repeat(var(--grid-items),minmax(0,1fr))}.channel{border-right:0}.card{grid-template-columns:1fr}.card-tags{text-align:left}.data-toolbar{flex-wrap:wrap}.workspace-sidebar{width:calc(100vw - 12px);top:52px}.sidebar-backdrop{inset:52px 0 0}.control-fields{grid-template-columns:1fr}}
    .layout-column > .view-switcher { flex:1 }
    .live-toggle { border:1px solid var(--line); border-radius:6px; padding:5px 9px; background:white; color:var(--muted); font:600 12px inherit; cursor:pointer } .live-toggle.active { border-color:#b42318; color:#b42318; background:#fff1f0 } html[data-theme="dark"] .live-toggle { background:#193741; color:var(--muted) } html[data-theme="dark"] .live-toggle.active { border-color:#ff7b72; color:#ff9b94; background:#4a2020 }
    .batch-menu[open] { z-index:35 }
    .batch-state.cancelling { color:#b7791f }
    .notification-job-log { margin-left:0 }
    .workspace-add-toggle { min-height:30px; padding:4px 10px; border:1px solid #b9d0d54d; border-radius:6px; color:#e7f1f3; background:#193741; font:600 12px system-ui,sans-serif; cursor:pointer } .workspace-add-toggle:hover { border-color:#8ed0d7; background:#214752 } .workspace-wizard { width:min(720px,calc(100vw - 28px)); max-height:calc(100vh - 36px); padding:0; overflow:hidden; border:1px solid var(--line); border-radius:12px; color:var(--ink); background:#fbfcfc; box-shadow:0 24px 70px #071b2260 } .workspace-wizard::backdrop { background:#071b2273; backdrop-filter:blur(3px) } html[data-theme="dark"] .workspace-wizard { background:#10252d } .workspace-wizard-form { display:flex; max-height:calc(100vh - 38px); flex-direction:column } .workspace-wizard-head { display:flex; align-items:start; gap:18px; padding:20px 22px 16px; border-bottom:1px solid var(--line) } .workspace-wizard-head>div { flex:1 } .workspace-wizard-head h1 { margin:0; font-size:22px } .workspace-wizard-head p { margin:4px 0 0; color:var(--muted); font-size:13px } .workspace-wizard-close { padding:0 5px; border:0; color:var(--muted); background:transparent; font-size:24px; line-height:1; cursor:pointer } .workspace-wizard-body { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:13px 14px; padding:18px 22px; overflow-y:auto } .workspace-wizard-body label,.workspace-wizard-field { display:flex; min-width:0; flex-direction:column; gap:4px; color:var(--muted); font-size:11px; font-weight:600 } .workspace-wizard-body input,.workspace-wizard-body select,.workspace-wizard-body textarea { width:100%; min-height:38px; padding:7px 9px; color:var(--ink); background:white; border:1px solid var(--line); border-radius:6px; font:13px system-ui,sans-serif } html[data-theme="dark"] .workspace-wizard-body input,html[data-theme="dark"] .workspace-wizard-body select,html[data-theme="dark"] .workspace-wizard-body textarea { background:#193741 } .workspace-wizard-wide { grid-column:1/-1 } .workspace-path-field { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:7px } .workspace-path-field button,.workspace-discover { min-height:38px; padding:6px 10px; border:1px solid var(--line); border-radius:6px; color:var(--ink); background:var(--wash); font:600 12px system-ui,sans-serif; cursor:pointer } .workspace-source-field { display:grid; grid-template-columns:minmax(0,1fr) auto auto; gap:7px } .workspace-config-details { grid-column:1/-1; border:1px solid var(--line); border-radius:7px; background:var(--wash) } .workspace-config-details summary { padding:9px 11px; color:var(--muted); font-size:12px; font-weight:650; cursor:pointer } .workspace-config-details label { padding:0 11px 11px } .workspace-config-details textarea { min-height:84px; resize:vertical; font:12px ui-monospace,monospace } .workspace-persist { grid-column:1/-1; display:grid!important; grid-template-columns:auto 1fr; align-items:center; gap:0 9px!important; padding:10px 11px; border:1px solid var(--line); border-radius:7px; background:var(--wash) } .workspace-persist input { width:17px; min-height:17px; grid-row:1/3; margin:0 } .workspace-persist strong { color:var(--ink); font-size:12px } .workspace-persist span { font-weight:400 } .workspace-wizard-error { grid-column:1/-1; margin:0; padding:9px 11px; border:1px solid #d13c32; border-radius:6px; color:#8c2e2e; background:#fff7f7; font-size:12px } html[data-theme="dark"] .workspace-wizard-error { color:#ff9b94; background:#4a2020 } .workspace-wizard-actions { display:flex; justify-content:flex-end; gap:8px; padding:13px 22px; border-top:1px solid var(--line); background:var(--wash) } .workspace-wizard-actions button { min-height:36px; padding:7px 13px; border:1px solid var(--line); border-radius:6px; color:var(--ink); background:white; font:600 13px system-ui,sans-serif; cursor:pointer } .workspace-wizard-actions .primary { color:white; background:var(--accent); border-color:var(--accent) } .workspace-wizard-actions button:disabled { opacity:.55; cursor:wait } .workspace-empty-action { margin-top:12px }
    @media(max-width:700px){.workspace-wizard-body{grid-template-columns:1fr}.workspace-wizard-body>*{grid-column:1}.workspace-source-field{grid-template-columns:minmax(0,1fr) auto}.workspace-source-field .workspace-discover{grid-column:1/-1}.workspace-wizard-head,.workspace-wizard-body,.workspace-wizard-actions{padding-left:14px;padding-right:14px}}
    ::view-transition-old(root),::view-transition-new(root) { animation-duration:100ms; animation-timing-function:ease-out }
  </style>
</head>
<body><header><button class="header-nav" id="header-back" type="button" aria-label="Back" title="Back"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg></button><button class="header-nav" id="header-forward" type="button" aria-label="Forward" title="Forward"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg></button><button class="header-nav" id="header-refresh" type="button" aria-label="Refresh" title="Refresh"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6v5h-5"/><path d="M19 11a8 8 0 1 0 1 5"/></svg></button><button class="home-title" id="app-home">__BROWSER_TITLE__</button><span id="app-subtitle">__BROWSER_SUBTITLE__</span><span class="header-spacer"></span><button class="workspace-add-toggle" id="workspace-add" type="button" hidden>+ Workspace</button><details class="notification-center" id="header-notifications"><summary aria-label="Notifications" title="Notifications"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></svg><span class="notification-badge" id="notification-badge" hidden>0</span></summary><div class="notification-popover"><div class="notification-head"><strong>Notifications</strong></div><div id="notification-list"><p class="notification-empty">No notifications yet.</p></div></div></details><select id="theme-toggle" aria-label="Color theme"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select><details class="header-menu" id="header-annotate" hidden><summary>Annotate</summary><form class="header-popover" id="annotation-form"></form></details><details class="header-menu" id="header-download" hidden><summary>Download</summary><form class="header-popover" id="download-form"></form></details><button class="sidebar-toggle" id="header-details" data-sidebar-toggle aria-expanded="false" hidden>Details</button><button class="fullscreen-toggle" id="fullscreen-toggle" aria-label="Enter fullscreen" aria-pressed="false">⛶</button></header><div class="notification-toasts" id="notification-toasts" aria-live="polite"></div>
<dialog class="workspace-wizard" id="workspace-wizard">
  <form class="workspace-wizard-form" id="workspace-wizard-form">
    <div class="workspace-wizard-head"><div><h1>Add workspace</h1><p>Pair a workspace implementation with local data for this running session.</p></div><button class="workspace-wizard-close" id="workspace-wizard-close" type="button" aria-label="Close">×</button></div>
    <div class="workspace-wizard-body">
      <div class="workspace-wizard-field workspace-wizard-wide"><span>Workspace repository <small>(optional)</small></span><div class="workspace-source-field"><input id="workspace-repository" type="text" placeholder="/path/to/workspace/repository"><button id="workspace-repository-browse" type="button" hidden>Browse</button><button class="workspace-discover" id="workspace-discover" type="button">Discover</button></div></div>
      <label class="workspace-wizard-wide">Workspace type<select id="workspace-factory" required></select></label>
      <div class="workspace-wizard-field workspace-wizard-wide"><span>Data directory <small>(optional)</small></span><div class="workspace-path-field"><input id="workspace-data-root" type="text" placeholder="/path/to/data"><button id="workspace-data-browse" type="button" hidden>Browse</button></div></div>
      <label>Instance name<input id="workspace-name" type="text" required></label>
      <label>Identifier<input id="workspace-id" type="text" required pattern="[A-Za-z0-9][A-Za-z0-9._-]*"></label>
      <label class="workspace-wizard-wide">Description<input id="workspace-description" type="text"></label>
      <label>Category<input id="workspace-category" type="text" placeholder="scientific data"></label>
      <label>Tags<input id="workspace-tags" type="text" placeholder="radar, field test"></label>
      <label class="workspace-persist"><input id="workspace-flatten" type="checkbox"><strong>Flatten discovery</strong><span>Show every discovered item in one list instead of preserving folders.</span></label>
      <details class="workspace-config-details"><summary>Additional factory configuration</summary><label>JSON object<textarea id="workspace-config">{}</textarea></label></details>
      <label class="workspace-persist"><input id="workspace-persist" type="checkbox"><strong>Save to a profile</strong><span>Append this workspace to TOML as well as opening it now.</span></label>
      <label class="workspace-wizard-wide" id="workspace-profile-field" hidden>Profile path<input id="workspace-profile-path" type="text"></label>
      <p class="workspace-wizard-error" id="workspace-wizard-error" hidden></p>
    </div>
    <div class="workspace-wizard-actions"><button id="workspace-wizard-cancel" type="button">Cancel</button><button class="primary" id="workspace-wizard-submit" type="submit">Open workspace</button></div>
  </form>
</dialog>
<main id="app"></main>
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
const workspaceAdd=document.querySelector('#workspace-add');
const workspaceWizard=document.querySelector('#workspace-wizard');
const workspaceWizardForm=document.querySelector('#workspace-wizard-form');
const workspaceFactory=document.querySelector('#workspace-factory');
const workspaceRepository=document.querySelector('#workspace-repository');
const workspaceDataRoot=document.querySelector('#workspace-data-root');
const workspaceFlatten=document.querySelector('#workspace-flatten');
const workspacePersist=document.querySelector('#workspace-persist');
const workspaceProfileField=document.querySelector('#workspace-profile-field');
const workspaceProfilePath=document.querySelector('#workspace-profile-path');
const workspaceWizardError=document.querySelector('#workspace-wizard-error');
const workspaceWizardSubmit=document.querySelector('#workspace-wizard-submit');
const fullscreenToggle=document.querySelector('#fullscreen-toggle');
const themeToggle=document.querySelector('#theme-toggle');let themePreference=localStorage.getItem('sigvue-theme')||'system',activeThemeRefresh=null,singleWorkspaceMode=false;
function resolvedTheme(){return themePreference==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):themePreference}function applyTheme(){document.documentElement.dataset.theme=resolvedTheme();themeToggle.value=themePreference}async function commitTheme(update){if(!document.startViewTransition){await update();return}const transition=document.startViewTransition(update);await transition.finished}async function refreshTheme(){if(activeThemeRefresh)await activeThemeRefresh();else applyTheme()}applyTheme();themeToggle.onchange=async()=>{themePreference=themeToggle.value;localStorage.setItem('sigvue-theme',themePreference);themeToggle.disabled=true;try{await refreshTheme()}catch(error){applyTheme();alert(`Theme refresh failed: ${error.message}`)}finally{themeToggle.disabled=false}};matchMedia('(prefers-color-scheme: dark)').addEventListener('change',async()=>{if(themePreference==='system'){try{await refreshTheme()}catch(error){applyTheme();console.error(error)}}});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=async path=>{const r=await fetch(path);if(!r.ok)throw new Error((await r.json()).detail||`Request failed (${r.status})`);return r.json()};
const apiPost=async(path,payload)=>{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(!r.ok)throw new Error((await r.json()).detail||`Request failed (${r.status})`);return r.json()};
let workspaceSetupState=null;
const workspaceNameFromFactory=value=>String(value||'workspace').split(/[-_.]+/).filter(Boolean).map(part=>part[0].toUpperCase()+part.slice(1)).join(' ');
const workspaceSlug=value=>String(value||'workspace').toLowerCase().trim().replace(/[^a-z0-9._-]+/g,'-').replace(/^[^a-z0-9]+|[^a-z0-9]+$/g,'')||'workspace';
function availableWorkspaceId(base){const used=new Set((workspaceSetupState?.workspaces||[]).map(workspace=>workspace.id)),slug=workspaceSlug(base);if(!used.has(slug))return slug;let suffix=2;while(used.has(`${slug}-${suffix}`))suffix++;return`${slug}-${suffix}`}
function selectedWorkspaceFactory(){return workspaceSetupState?.factories?.[Number(workspaceFactory.value)]}
function applyWorkspaceFactoryDefaults(){const factory=selectedWorkspaceFactory();if(!factory)return;const defaults=factory.defaults||{},configuration={...(defaults.config||{})},dataRoot=configuration.data_root||'';delete configuration.data_root;const name=defaults.name||workspaceNameFromFactory(factory.name);document.querySelector('#workspace-name').value=name;document.querySelector('#workspace-id').value=availableWorkspaceId(defaults.id||factory.name);document.querySelector('#workspace-description').value=defaults.description||'';document.querySelector('#workspace-category').value=defaults.category||'';document.querySelector('#workspace-tags').value=(defaults.tags||[]).join(', ');workspaceDataRoot.value=dataRoot;workspaceFlatten.checked=Boolean(defaults.flatten_discovery);document.querySelector('#workspace-config').value=JSON.stringify(configuration,null,2)}
function setWorkspaceWizardError(error=null){workspaceWizardError.hidden=!error;workspaceWizardError.textContent=error?.message||String(error||'')}
async function loadWorkspaceFactories(){setWorkspaceWizardError();const repository=workspaceRepository.value.trim(),query=repository?`?repository=${encodeURIComponent(repository)}`:'';workspaceSetupState=await api(`/workspace-setup${query}`);workspaceFactory.innerHTML=(workspaceSetupState.factories||[]).map((factory,index)=>`<option value="${index}">${esc(factory.name)}${factory.package?` · ${esc(factory.package)}`:''}</option>`).join('');workspaceFactory.disabled=!workspaceSetupState.factories?.length;workspaceWizardSubmit.disabled=!workspaceSetupState.factories?.length;if(!workspaceSetupState.factories?.length)workspaceFactory.innerHTML='<option>No workspace factories found</option>';workspaceProfilePath.value=workspaceSetupState.default_profile_path||'';applyWorkspaceFactoryDefaults()}
function nativeDirectoryPickerAvailable(){return Boolean(window.pywebview?.api?.choose_directory)}
function syncDirectoryPickerButtons(){document.querySelector('#workspace-repository-browse').hidden=!nativeDirectoryPickerAvailable();document.querySelector('#workspace-data-browse').hidden=!nativeDirectoryPickerAvailable()}
async function chooseWorkspaceDirectory(input){const selected=await window.pywebview?.api?.choose_directory?.();const value=Array.isArray(selected)?selected[0]:selected;if(value)input.value=value}
async function openWorkspaceWizard(){workspaceWizardForm.reset();document.querySelector('#workspace-config').value='{}';workspaceProfileField.hidden=true;setWorkspaceWizardError();workspaceWizard.showModal();syncDirectoryPickerButtons();try{await loadWorkspaceFactories()}catch(error){setWorkspaceWizardError(error);workspaceWizardSubmit.disabled=true}}
function closeWorkspaceWizard(){workspaceWizard.close();setWorkspaceWizardError()}
function showWorkspaceToast(workspace,persistedTo){const toast=document.createElement('div');toast.className='notification-toast';toast.innerHTML=`<strong>${esc(workspace.name)} opened</strong><span>${persistedTo?`Saved to ${esc(persistedTo)}`:'Available for this session.'}</span>`;notificationToasts.append(toast);setTimeout(()=>toast.remove(),3000)}
workspaceAdd.onclick=openWorkspaceWizard;
document.querySelector('#workspace-wizard-close').onclick=closeWorkspaceWizard;
document.querySelector('#workspace-wizard-cancel').onclick=closeWorkspaceWizard;
document.querySelector('#workspace-discover').onclick=async()=>{try{await loadWorkspaceFactories()}catch(error){setWorkspaceWizardError(error)}};
document.querySelector('#workspace-repository-browse').onclick=()=>chooseWorkspaceDirectory(workspaceRepository);
document.querySelector('#workspace-data-browse').onclick=()=>chooseWorkspaceDirectory(workspaceDataRoot);
workspaceFactory.onchange=applyWorkspaceFactoryDefaults;
workspacePersist.onchange=()=>workspaceProfileField.hidden=!workspacePersist.checked;
workspaceWizard.onclick=event=>{if(event.target===workspaceWizard)closeWorkspaceWizard()};
window.addEventListener('pywebviewready',syncDirectoryPickerButtons);
workspaceWizardForm.onsubmit=async event=>{
  event.preventDefault();setWorkspaceWizardError();workspaceWizardSubmit.disabled=true;workspaceWizardSubmit.textContent=workspacePersist.checked?'Saving…':'Opening…';
  try{
    const factory=selectedWorkspaceFactory();if(!factory)throw new Error('Choose a workspace type');
    let configuration;
    try{configuration=JSON.parse(document.querySelector('#workspace-config').value||'{}')}catch(error){throw new Error(`Additional configuration must be valid JSON: ${error.message}`)}
    if(!configuration||Array.isArray(configuration)||typeof configuration!=='object')throw new Error('Additional configuration must be a JSON object');
    if(workspaceDataRoot.value.trim())configuration.data_root=workspaceDataRoot.value.trim();
    const payload={use:factory.use,id:document.querySelector('#workspace-id').value.trim(),name:document.querySelector('#workspace-name').value.trim(),description:document.querySelector('#workspace-description').value.trim(),category:document.querySelector('#workspace-category').value.trim(),tags:document.querySelector('#workspace-tags').value.split(',').map(value=>value.trim()).filter(Boolean),flatten_discovery:workspaceFlatten.checked,config:configuration,persist:workspacePersist.checked,profile_path:workspacePersist.checked?workspaceProfilePath.value.trim():null};
    if(!payload.description)delete payload.description;if(!payload.category)delete payload.category;if(!payload.tags.length)delete payload.tags;const repository=workspaceRepository.value.trim()||factory.repository;if(repository)payload.path=repository;
    const result=await apiPost('/workspaces',payload);closeWorkspaceWizard();const current=await api('/workspaces');singleWorkspaceMode=current.workspaces.length===1;showWorkspaceToast(result.workspace,result.persisted_to);await items(result.workspace.id,result.workspace.name,true,[])
  }catch(error){setWorkspaceWizardError(error)}
  finally{workspaceWizardSubmit.disabled=false;workspaceWizardSubmit.textContent='Open workspace'}
};
const nativePushState=history.pushState.bind(history);let routeIndex=Number(history.state?.sigvueIndex??0),routeMaximum=Number(sessionStorage.getItem('sigvue-route-maximum')??routeIndex);if(!Number.isFinite(routeIndex))routeIndex=0;if(!Number.isFinite(routeMaximum)||routeMaximum<routeIndex)routeMaximum=routeIndex;if(history.state?.sigvueIndex==null)history.replaceState({...history.state,sigvueIndex:routeIndex},'',location.href);
function syncHeaderNavigation(){headerBack.disabled=routeIndex<=0;headerForward.disabled=routeIndex>=routeMaximum}
history.pushState=(state,title,path)=>{routeIndex+=1;routeMaximum=routeIndex;sessionStorage.setItem('sigvue-route-maximum',String(routeMaximum));nativePushState({...state,sigvueIndex:routeIndex},title,path);syncHeaderNavigation()};
function pushRoute(path){history.pushState(null,'',path)}
function followInternalResultLink(event){const link=event.target.closest?.('a[href]');if(!link||event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||link.target==='_blank'||link.hasAttribute('download'))return false;const target=new URL(link.href,location.href);if(target.origin!==location.origin||!target.pathname.startsWith('/results/'))return false;event.preventDefault();pushRoute(`${target.pathname}${target.search}${target.hash}`);void boot();return true}
document.addEventListener('click',followInternalResultLink,true);
headerBack.onclick=()=>history.back();
headerForward.onclick=()=>history.forward();
headerRefresh.onclick=async()=>{headerRefresh.disabled=true;try{await boot(true)}finally{headerRefresh.disabled=false}};
const fail=e=>app.innerHTML=`<div class="error"><b>Unable to load this page</b><br>${esc(e.message)}</div>`;
let playbackTimer=null,resultBrowserTimer=null,resultBrowserGeneration=0,playbackPosition=0,playbackPaused=false,playbackFollowLive=false,windowStart=0,windowEnd=null,segmentId=null,segmentedPlaybackGeneration=0,plotResizeObserver=null,windowOverviewResizeObserver=null,itemActionResizeObserver=null,redrawWindowOverview=null,dataStageResizeFrame=null,resultBrowserResizeFrame=null,annotations=[],annotationTimelineColorControl=null,activePlaybackSeek=null,activeAnnotationSeek=null;
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
function fixedSegmentTime(seconds,config){const value=displayTime(seconds,config);return Number.isFinite(value)?value.toFixed(2):'0.00'}
function formatSegmentRange(segment,duration,config){const start=Number(segment.start_seconds)||0,stop=start+(Number(segment.duration_seconds)||0);return `${fixedSegmentTime(start,config)}–${fixedSegmentTime(stop,config)} / ${fixedSegmentTime(duration,config)} ${timelineSpec(config).label}`}
function choiceOptions(choices){return (choices||[]).map(choice=>`<option value="${esc(choice.value)}">${esc(choice.label)}</option>`).join('')}
function annotationFieldHtml(field){const required=field.required?'required':'',value=esc(field.default||'');if(field.field_type==='select')return `<label>${esc(field.label)}<select data-annotation-field="${esc(field.name)}" ${required}>${choiceOptions(field.options)}</select></label>`;if(field.field_type==='textarea')return `<label>${esc(field.label)}<textarea data-annotation-field="${esc(field.name)}" ${required}>${value}</textarea></label>`;return `<label>${esc(field.label)}<input ${field.field_type==='number'?'type="number" step="any"':''} data-annotation-field="${esc(field.name)}" value="${value}" ${required}></label>`}
function axisName(value,orientation){const raw=String(value?._name||value?._id||value||'');if(raw===orientation)return`${orientation}axis`;if(new RegExp(`^${orientation}\\d+$`).test(raw))return`${orientation}axis${raw.slice(1)}`;return new RegExp(`^${orientation}axis\\d*$`).test(raw)?raw:null}
function matchedAxisRoot(plot,name){let current=name;const seen=new Set();while(current&&!seen.has(current)){seen.add(current);const match=plot?._fullLayout?.[current]?.matches,next=axisName(match,current[0]);if(!next)return current;current=next}return current||name}
function inferredSelectionAxis(plot,orientation,range,event){const point=(event?.points||[]).find(candidate=>candidate?.[`${orientation}axis`]),fromPoint=axisName(point?.[`${orientation}axis`],orientation);if(fromPoint)return fromPoint;const low=Math.min(...range.map(Number)),high=Math.max(...range.map(Number)),candidates=Object.keys(plot?._fullLayout||{}).filter(name=>new RegExp(`^${orientation}axis\\d*$`).test(name)).filter(name=>{const bounds=plot._fullLayout[name]?.range;if(!Array.isArray(bounds))return false;const minimum=Math.min(...bounds.map(Number)),maximum=Math.max(...bounds.map(Number));return low>=minimum&&high<=maximum});return candidates.length===1?candidates[0]:`${orientation}axis`}
function selectedPlotRanges(plot,event){const result={};for(const [key,range] of Object.entries(event?.range||{})){if(!Array.isArray(range)||range.length<2||!['x','y'].includes(key[0]))continue;const orientation=key[0],explicit=axisName(key,orientation),name=key.length>1&&explicit?explicit:inferredSelectionAxis(plot,orientation,range,event);result[name]=range.map(Number)}return result}
function plotSelectionRange(plot,view,axis){const ranges=plotSelections.get(view);if(!ranges)return null;if(Array.isArray(ranges[axis]))return ranges[axis];const root=matchedAxisRoot(plot,axis);for(const [candidate,range] of Object.entries(ranges)){if(candidate[0]===axis[0]&&matchedAxisRoot(plot,candidate)===root)return range}return null}
function plotResetState(view){const layout=view?.value?.layout||{},reset={},bounds={};for(const [name,axis] of Object.entries(layout)){if(!/^[xy]axis\d*$/.test(name)||!Array.isArray(axis?.range)||axis.range.length<2)continue;const range=axis.range.map(Number);if(range.every(Number.isFinite)){reset[`${name}.range`]=range;reset[`${name}.autorange`]=false;if(view?.axis_navigation==='bounded')bounds[name]=range}}return{reset,bounds}}
function rememberPlotResetRanges(plot,view){const state=plotResetState(view);plot._sigvueResetRanges=state.reset;plot._sigvueAxisBounds=state.bounds}
function currentPlotViewport(plot){return Object.fromEntries(Object.entries(plot?._sigvueViewport||{}).map(([name,range])=>[name,[...range]]))}
function translatedAxisRange(range,previous,current){if(!Array.isArray(range)||!Array.isArray(previous)||!Array.isArray(current))return range;const oldWidth=previous[1]-previous[0],newWidth=current[1]-current[0];if(![...range,...previous,...current,oldWidth,newWidth].every(Number.isFinite)||Math.abs(oldWidth)<=Number.EPSILON)return range;const reversed=range[0]>range[1],span=Math.abs(range[1]-range[0]),centerFraction=((range[0]+range[1])/2-previous[0])/oldWidth,center=current[0]+centerFraction*newWidth,candidate=reversed?[center+span/2,center-span/2]:[center-span/2,center+span/2];return clampedAxisRange(candidate,current)}
function plotAxisNames(view){const names=new Set(Object.keys(view?.value?.layout||{}).filter(name=>/^[xy]axis\d*$/.test(name)));for(const trace of view?.value?.data||[]){for(const orientation of['x','y']){const name=axisName(trace?.[`${orientation}axis`]||orientation,orientation);if(name)names.add(name)}}return names}
function restoredPlotViewport(view,viewport,previousReset,currentReset,bounds){const update={},axes=plotAxisNames(view);for(const [name,range] of Object.entries(viewport||{})){if(!axes.has(name)||!Array.isArray(range))continue;const previous=previousReset?.[`${name}.range`],current=currentReset?.[`${name}.range`],translated=translatedAxisRange(range,previous,current),limit=bounds?.[name],restored=limit?clampedAxisRange(translated,limit):translated;update[`${name}.range`]=restored;update[`${name}.autorange`]=false}return update}
function requestsPlotReset(event){return Object.entries(event||{}).some(([key,value])=>key.endsWith('.autorange')&&value===true)}
function resetPlotAxes(plot,onReset){const update=plot._sigvueResetRanges||{};if(plot._sigvueResetting||!Object.keys(update).length)return;plot._sigvueResetting=true;Promise.resolve(Plotly.relayout(plot,update)).finally(()=>{plot._sigvueResetting=false;onReset?.()})}
function relayoutAxisRange(event,name){const combined=event?.[`${name}.range`],low=event?.[`${name}.range[0]`],high=event?.[`${name}.range[1]`];return Array.isArray(combined)?combined.map(Number):Number.isFinite(Number(low))&&Number.isFinite(Number(high))?[Number(low),Number(high)]:null}
function capturePlotViewport(plot,event){const viewport={...(plot._sigvueViewport||{})};for(const name of Object.keys(plot?._fullLayout||{})){if(!/^[xy]axis\d*$/.test(name))continue;const range=relayoutAxisRange(event,name);if(range?.every(Number.isFinite))viewport[name]=range}plot._sigvueViewport=viewport;return viewport}
function clampedAxisRange(range,bounds){const reversed=range[0]>range[1],requested=[Math.min(...range),Math.max(...range)],allowed=[Math.min(...bounds),Math.max(...bounds)],allowedWidth=allowed[1]-allowed[0],width=requested[1]-requested[0];let result;if(width>=allowedWidth)result=allowed;else{let low=requested[0],high=requested[1];if(low<allowed[0]){high+=allowed[0]-low;low=allowed[0]}if(high>allowed[1]){low-=high-allowed[1];high=allowed[1]}result=[low,high]}return reversed?result.reverse():result}
function constrainPlotDuringPan(plot,event){if(plot._sigvueClamping)return;const update={};for(const [name,bounds] of Object.entries(plot._sigvueAxisBounds||{})){const range=relayoutAxisRange(event,name);if(!range)continue;const clamped=clampedAxisRange(range,bounds);if(clamped.some((value,index)=>Math.abs(value-range[index])>1e-12))update[`${name}.range`]=clamped}if(!Object.keys(update).length)return;plot._sigvueClamping=true;Promise.resolve(Plotly.relayout(plot,update)).finally(()=>{plot._sigvueClamping=false})}
function bindPlotSelection(plot,onViewportChanged){if(plot.dataset.plotSelectionBound)return;plot.dataset.plotSelectionBound='true';plot.on('plotly_selected',event=>{const ranges=selectedPlotRanges(plot,event);if(Object.keys(ranges).length)plotSelections.set(plot.dataset.plotView,ranges)});plot.on('plotly_relayouting',event=>constrainPlotDuringPan(plot,event));plot.on('plotly_relayout',event=>{if(plot._sigvueUpdating||plot._sigvueResetting)return;if(requestsPlotReset(event)){plotSelections.delete(plot.dataset.plotView);plot._sigvueViewport={};resetPlotAxes(plot,onViewportChanged);return}if(Object.keys(capturePlotViewport(plot,event)).length)onViewportChanged?.()});const clear=()=>plotSelections.delete(plot.dataset.plotView);plot.on('plotly_deselect',clear);plot.on('plotly_doubleclick',()=>{clear();plot._sigvueViewport={};resetPlotAxes(plot,onViewportChanged);return false})}
function annotationBoundPlot(binding){const exact=[...document.querySelectorAll('[data-plot-view]')].find(candidate=>candidate.dataset.plotView===binding.view);if(exact)return exact;const switcher=[...document.querySelectorAll('[data-view-switcher]')].find(candidate=>candidate.dataset.viewSwitcher===binding.view);return switcher?.querySelector(':scope > .view-pane.active [data-plot-view]')||null}
function populatePlotBoundAnnotationFields(page){let populated=0;for(const field of page.annotation?.fields||[]){const binding=field.plot_binding;if(!binding)continue;const plot=annotationBoundPlot(binding),selected=binding.selection_policy==='box_preferred'?plotSelectionRange(plot,plot?.dataset.plotView||binding.view,binding.axis):null,range=selected||plot?._fullLayout?.[binding.axis]?.range;if(!Array.isArray(range)||range.length<2)continue;const edges=range.map(Number).filter(Number.isFinite).sort((a,b)=>a-b),edge=binding.edge==='lower'?edges[0]:edges.at(-1),dynamicOffset=binding.offset_source==='playback'?playbackPosition:0,value=edge*Number(binding.scale??1)+Number(binding.offset??0)+dynamicOffset,input=[...document.querySelectorAll('[data-annotation-field]')].find(candidate=>candidate.dataset.annotationField===field.name);if(input&&Number.isFinite(value)){input.value=Number(value.toPrecision(12));populated++}}return populated}
function configureCapabilityMenus(page){const annotationForm=document.querySelector('#annotation-form'),downloadForm=document.querySelector('#download-form');annotationForm.innerHTML=(page.annotation?.fields||[]).map(annotationFieldHtml).join('')+'<button class="primary" type="submit">Add annotation</button>';headerAnnotate.ontoggle=()=>{if(headerAnnotate.open&&!populatePlotBoundAnnotationFields(page))setTimeout(()=>{if(headerAnnotate.open)populatePlotBoundAnnotationFields(page)},100)};downloadForm.innerHTML=`<label>Data<select id="export-scope">${choiceOptions(page.export?.scopes)}</select></label><label>Format<select id="export-format">${choiceOptions(page.export?.formats)}</select></label><button class="primary" type="submit">Download</button>`}
function markdown(value){return esc(value).replace(/^### (.+)$/gm,'<h3>$1</h3>').replace(/^## (.+)$/gm,'<h2>$1</h2>').replace(/^# (.+)$/gm,'<h1>$1</h1>').replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\n/g,'<br>')}
function plotlyFigure(figure,viewName){const id=`plotly-${encodeURIComponent(viewName)}`;return `<div id="${id}" class="plotly-view" data-plot-view="${esc(viewName)}"></div>`}
function matplotlibFigure(payload,viewName){return `<img class="matplotlib-view" data-matplotlib-view="${esc(viewName)}" alt="${esc(viewName)}" src="data:image/png;base64,${payload}">`}
const plotlyConfig={responsive:true,displaylogo:false,doubleClick:'reset',modeBarButtonsToAdd:['select2d'],modeBarButtonsToRemove:['lasso2d']};
function managedPlotlyLayout(view){const layout={...(view?.value?.layout||{})};delete layout.uirevision;delete layout.width;delete layout.height;layout.autosize=true;return layout}
function layoutWithPlotViewport(view,viewport){const layout=managedPlotlyLayout(view);for(const [key,value] of Object.entries(viewport||{})){const match=/^([xy]axis\d*)\.(range|autorange)$/.exec(key);if(!match)continue;const[,name,property]=match;layout[name]={...(layout[name]||{}),[property]:value}}return layout}
function setClientRuntime(name,milliseconds){const target=document.querySelector(`[data-client-stat="${name}"]`);if(target)target.textContent=`${milliseconds.toFixed(1)} ms`;if(name==='browser-runtime'){const total=document.querySelector('[data-client-stat="total-runtime"]');if(total)total.textContent=`${(currentServerRuntime+milliseconds).toFixed(1)} ms`}}
function setPlotlyRuntime(started){setClientRuntime('plotly-runtime',performance.now()-started)}
async function initializePlotlyViews(views,onViewportChanged){const started=performance.now(),jobs=[];document.querySelectorAll('[data-plot-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.plotView);if(view&&view.kind==='plotly')jobs.push(Plotly.newPlot(target,view.value.data||[],managedPlotlyLayout(view),plotlyConfig).then(()=>{target._sigvueViewport={};rememberPlotResetRanges(target,view);bindPlotSelection(target,view.rasterized?onViewportChanged:null)}))});await Promise.all(jobs);setPlotlyRuntime(started)}
async function updatePlotlyViews(views){const started=performance.now(),jobs=[];document.querySelectorAll('[data-plot-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.plotView);if(view&&view.kind==='plotly'){const previous=target._sigvueResetRanges,viewport=currentPlotViewport(target),state=plotResetState(view),restored=restoredPlotViewport(view,viewport,previous,state.reset,state.bounds),layout=layoutWithPlotViewport(view,restored);target._sigvueResetRanges=state.reset;target._sigvueAxisBounds=state.bounds;target._sigvueViewport=Object.fromEntries(Object.entries(restored).filter(([key])=>key.endsWith('.range')).map(([key,range])=>[key.slice(0,-6),range]));target._sigvueUpdating=true;jobs.push(Plotly.react(target,view.value.data||[],layout,plotlyConfig).finally(()=>{target._sigvueUpdating=false}))}});await Promise.all(jobs);setPlotlyRuntime(started)}
async function updateMatplotlibViews(views){const jobs=[];document.querySelectorAll('[data-matplotlib-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.matplotlibView);if(view&&view.kind==='matplotlib'){const source=`data:image/png;base64,${view.value}`;jobs.push(new Promise(resolve=>{target.onload=target.onerror=resolve;target.src=source;if(target.complete)resolve()}))}});await Promise.all(jobs)}
async function preloadMatplotlibViews(views){const sources=views.filter(view=>view.kind==='matplotlib').map(view=>`data:image/png;base64,${view.value}`);await Promise.all(sources.map(source=>new Promise(resolve=>{const image=new Image();image.onload=image.onerror=resolve;image.src=source;if(image.complete)resolve()})))}
function resizePlots(){document.querySelectorAll('[data-plot-view]').forEach(target=>Plotly.Plots.resize(target))}
function sizeDataStage(){const stage=document.querySelector('.data-stage');if(!stage)return;const available=Math.max(280,Math.floor(window.innerHeight-stage.getBoundingClientRect().top-4));stage.style.height=`${available}px`;cancelAnimationFrame(dataStageResizeFrame);dataStageResizeFrame=requestAnimationFrame(resizePlots)}
function observeDataStage(){plotResizeObserver?.disconnect();const stage=document.querySelector('.data-stage');if(!stage)return;plotResizeObserver=new ResizeObserver(()=>{cancelAnimationFrame(dataStageResizeFrame);dataStageResizeFrame=requestAnimationFrame(resizePlots)});plotResizeObserver.observe(stage);window.addEventListener('resize',sizeDataStage,{passive:true});sizeDataStage()}
function stopPlayback(){if(app.classList.contains('item-page'))document.body.classList.add('hold-item-layout');segmentedPlaybackGeneration++;resultBrowserGeneration++;clearInterval(playbackTimer);playbackTimer=null;clearTimeout(resultBrowserTimer);resultBrowserTimer=null;activePlaybackSeek=null;activeAnnotationSeek=null;plotResizeObserver?.disconnect();plotResizeObserver=null;windowOverviewResizeObserver?.disconnect();windowOverviewResizeObserver=null;itemActionResizeObserver?.disconnect();itemActionResizeObserver=null;cancelAnimationFrame(dataStageResizeFrame);dataStageResizeFrame=null;cancelAnimationFrame(resultBrowserResizeFrame);resultBrowserResizeFrame=null;window.removeEventListener('resize',sizeDataStage);window.removeEventListener('resize',sizeResultBrowser)}
function syncFullscreenToggle(){const active=Boolean(document.fullscreenElement);fullscreenToggle.setAttribute('aria-label',active?'Exit fullscreen':'Enter fullscreen');fullscreenToggle.setAttribute('aria-pressed',String(active));fullscreenToggle.textContent=active?'×':'⛶';sizeDataStage()}
fullscreenToggle.onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen()}catch(e){/* Browser fullscreen can be unavailable in embedded contexts. */}};
document.addEventListener('fullscreenchange',syncFullscreenToggle);
function tableRows(value){if(Array.isArray(value))return value;if(!value||typeof value!=='object')return[];const columns=Object.keys(value),indices=[...new Set(columns.flatMap(column=>Object.keys(value[column]||{})))];return indices.map(index=>Object.fromEntries(columns.map(column=>[column,value[column]?.[index]])))}
function tableHtml(value){const rows=tableRows(value);if(!rows.length)return '<div class="empty">No rows</div>';const columns=[...new Set(rows.flatMap(row=>Object.keys(row)))];return `<div class="table-wrap"><table class="data-table"><thead><tr>${columns.map(column=>`<th>${esc(column)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${columns.map(column=>`<td>${esc(statText(row[column]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}
function renderValue(v){if(v.kind==='markdown')return `<article class="prose">${markdown(v.value)}</article>`;if(v.kind==='text')return `<div class="text-view">${esc(v.value)}</div>`;if(v.kind==='table'||v.kind==='dataframe')return tableHtml(v.value);return `<pre>${esc(typeof v.value==='string'?v.value:JSON.stringify(v.value,null,2))}</pre>`}
function renderView(v){if(v.kind==='plotly')return plotlyFigure(v.value,v.name);if(v.kind==='matplotlib')return matplotlibFigure(v.value,v.name);return `<div data-render-view="${esc(v.name)}">${renderValue(v)}</div>`}
function mountRenderedViews(views){const mounted=[];for(const view of views){const slot=[...document.querySelectorAll('[data-view-slot]')].find(candidate=>candidate.dataset.viewSlot===view.name);if(slot&&!slot.firstElementChild){slot.innerHTML=renderView(view);mounted.push(view)}}return mounted}
function updateGenericViews(views){document.querySelectorAll('[data-render-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.renderView);if(view&&view.kind!=='plotly'&&view.kind!=='matplotlib')target.innerHTML=renderValue(view)})}
function gridTemplate(columns){if(Array.isArray(columns))return columns.map(weight=>`minmax(0,${Number(weight)||1}fr)`).join(' ');const count=Math.max(1,Number(columns)||1);return `repeat(${count},minmax(0,1fr))`}
function renderLayout(node,views,controls,values){if(node.kind==='view_slot'){const view=views.find(v=>v.name===node.view);return `<div class="view-slot" data-view-slot="${esc(node.view)}">${view?renderView(view):''}</div>`}if(node.kind==='control_slot'){const control=controls.find(candidate=>candidate.name===node.props.name);return control?(['colormap','limits'].includes(control.control_type)?customControlHtml(control,values):`<label class="parameter-control">${esc(control.label||controlLabel(control.name))}${controlHtml(control,values)}</label>`):''}if(node.kind==='tabs'){const key=String(node.props.selection_key||'__tabs'),selected=Math.min(Math.max(0,Number(viewSelections[key]||0)),Math.max(0,node.children.length-1)),labels=node.children.map((child,i)=>child.props.label||`Tab ${i+1}`);return `<div class="layout-tabs" data-layout-tabs="${esc(key)}"><nav class="tabs">${labels.map((label,i)=>`<button class="tab ${i===selected?'active':''}" data-layout-tab="${i}">${esc(label)}</button>`).join('')}</nav><div class="layout-tab-panes">${node.children.map((child,i)=>`<div class="layout-tab-pane ${i===selected?'active':''}" data-layout-pane="${i}" aria-hidden="${i!==selected}">${renderLayout(child,views,controls,values)}</div>`).join('')}</div></div>`}if(node.kind==='view_switcher'){const key=String(node.props.key),dimensionLabels=Array.isArray(node.props.labels)?node.props.labels:[node.props.label||'View'],selectors=Array.isArray(node.props.selectors)?node.props.selectors:[node.props.selector||'buttons'],options=Array.isArray(node.props.options)?node.props.options:[node.children.map((child,i)=>child.props.label||`View ${i+1}`)],coordinates=Array.isArray(node.props.coordinates)?node.props.coordinates:node.children.map((_,i)=>[i]),selectionKeys=Array.isArray(node.props.selection_keys)?node.props.selection_keys:[key],selected=selectionKeys.map(selectionKey=>Number(viewSelections[selectionKey]||0)),control=dimension=>selectors[dimension]==='dropdown'?`<select class="view-switcher-select" data-view-select data-view-dimension="${dimension}">${options[dimension].map((choice,i)=>`<option value="${i}" ${i===selected[dimension]?'selected':''}>${esc(choice)}</option>`).join('')}</select>`:options[dimension].map((choice,i)=>`<button class="view-choice ${i===selected[dimension]?'active':''}" data-view-choice="${i}" data-view-dimension="${dimension}">${esc(choice)}</button>`).join(''),active=coordinate=>coordinate.every((choice,dimension)=>Number(choice)===selected[dimension]);return `<div class="view-switcher" data-view-switcher="${esc(key)}" data-view-selection-keys="${esc(selectionKeys.join(','))}"><div class="view-switcher-head">${dimensionLabels.map((dimensionLabel,dimension)=>`<span class="view-switcher-label">${esc(dimensionLabel)}</span>${control(dimension)}`).join('')}</div>${node.children.map((child,i)=>`<div class="view-pane ${active(coordinates[i])?'active':''}" data-view-pane="${i}" data-view-coordinates="${esc(coordinates[i].join(','))}" data-view-label="${esc(child.props.label||`View ${i+1}`)}" aria-hidden="${!active(coordinates[i])}">${renderLayout(child,views,controls,values)}</div>`).join('')}</div>`}const children=node.children.map(child=>renderLayout(child,views,controls,values)).join('');if(node.kind==='control_group')return `<div class="parameter-group" style="--parameter-columns:${Number(node.props.columns)||1}">${node.props.label?`<div class="parameter-group-title">${esc(node.props.label)}</div>`:''}${children}</div>`;if(node.kind==='grid'){const columnCount=Array.isArray(node.props.columns)?node.props.columns.length:Number(node.props.columns)||1,rowCount=Math.ceil(node.children.length/columnCount);return `<div class="playback-grid ${node.children.length===1?'single-plot':''}" style="--grid-template:${gridTemplate(node.props.columns)};--grid-rows:repeat(${rowCount},minmax(0,1fr));--grid-items:${node.children.length}">${node.children.map(child=>`<div class="channel">${renderLayout(child,views,controls,values)}</div>`).join('')}</div>`}if(node.kind==='column'||node.kind==='stack')return `<div class="layout-column">${children}</div>`;if(node.kind==='row')return `<div class="layout-row">${children}</div>`;if(node.kind==='panel')return `<div class="layout-panel">${children}</div>`;return children}
function bindLayoutTabs(onActivate){document.querySelectorAll('[data-layout-tabs]').forEach(root=>{const key=root.dataset.layoutTabs||'__tabs',buttons=root.querySelectorAll(':scope > .tabs > [data-layout-tab]'),panes=root.querySelectorAll(':scope > .layout-tab-panes > [data-layout-pane]');buttons.forEach(button=>button.onclick=()=>{const selected=Number(button.dataset.layoutTab);viewSelections[key]=selected;buttons.forEach((candidate,index)=>candidate.classList.toggle('active',index===selected));panes.forEach((pane,index)=>{pane.classList.toggle('active',index===selected);pane.setAttribute('aria-hidden',String(index!==selected))});onActivate?.();requestAnimationFrame(resizePlots)})})}
function bindViewSwitchers(onActivate){document.querySelectorAll('.view-switcher[data-view-switcher]').forEach(root=>{const selectionKeys=String(root.dataset.viewSelectionKeys||root.dataset.viewSwitcher).split(','),selected=dimension=>Number(viewSelections[selectionKeys[dimension]]||0),activate=(dimension,value)=>{viewSelections[selectionKeys[dimension]]=value;root.querySelectorAll(':scope > .view-switcher-head [data-view-choice]').forEach(choice=>choice.classList.toggle('active',Number(choice.dataset.viewDimension)===dimension?Number(choice.dataset.viewChoice)===value:choice.classList.contains('active')));root.querySelectorAll(':scope > .view-switcher-head [data-view-select]').forEach(select=>{if(Number(select.dataset.viewDimension)===dimension)select.value=value});root.querySelectorAll(':scope > [data-view-pane]').forEach(pane=>{const coordinate=String(pane.dataset.viewCoordinates||pane.dataset.viewPane).split(',').map(Number),active=coordinate.every((choice,index)=>choice===selected(index));pane.classList.toggle('active',active);pane.setAttribute('aria-hidden',String(!active))});redrawWindowOverview?.();onActivate?.();requestAnimationFrame(resizePlots)};root.querySelectorAll(':scope > .view-switcher-head [data-view-choice]').forEach(button=>button.onclick=()=>activate(Number(button.dataset.viewDimension||0),Number(button.dataset.viewChoice)));root.querySelectorAll(':scope > .view-switcher-head [data-view-select]').forEach(select=>select.onchange=()=>activate(Number(select.dataset.viewDimension||0),Number(select.value)))})}
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
function startFrameworkWindowed(config,refresh,controls=[]){
  windowOverviewResizeObserver?.disconnect();redrawWindowOverview=null;const root=document.querySelector('#windowed-bar'),track=root?.querySelector('#windowed-track');if(!root||!track)return;const duration=Number(config.duration_seconds)||0,minimum=Math.max(Number(config.minimum_window_seconds)||0,1e-12),step=Number(config.step_seconds)||minimum;
  if(windowEnd==null){windowStart=Number(config.window_start_seconds)||0;windowEnd=Number(config.window_end_seconds)||Math.min(duration,windowStart+minimum)}
  const canvas=track.querySelector('canvas'),selection=track.querySelector('#windowed-selection'),left=track.querySelector('#windowed-left'),right=track.querySelector('#windowed-right'),fullExtent=track.querySelector('#windowed-full-extent'),startInput=root.querySelector('#windowed-start'),endInput=root.querySelector('#windowed-end'),totalLabel=root.querySelector('#windowed-total'),widthInput=root.querySelector('#windowed-width'),unitLabel=root.querySelector('#windowed-unit');let drag=null,updating=false,pending=false,commitTimer=null,heatmapCanvas=null;
  const clamp=()=>{windowStart=Math.min(duration-minimum,Math.max(0,Number(windowStart)||0));windowEnd=Math.min(duration,Math.max(windowStart+minimum,Number(windowEnd)||minimum))};
  const selectedOverviewIndex=()=>{const series=config.overview_series||[];return Math.min(Math.max(0,Number(viewSelections[config.overview_switcher_key]||0)),Math.max(0,series.length-1))};
  const selectedOverview=()=>{const series=config.overview_series||[];return series[selectedOverviewIndex()]||config.overview_values||[]};
  const selectedDuration=()=>{const durations=config.overview_durations_seconds||[],value=Number(durations[selectedOverviewIndex()]);return Number.isFinite(value)&&value>0?value:duration};
  const displayedWindow=()=>{const available=selectedDuration(),width=Math.min(available,windowEnd-windowStart),start=Math.min(Math.max(0,available-width),Math.max(0,windowStart));return[start,start+width]};
  const controlInput=name=>name?[...document.querySelectorAll('[data-control]')].find(candidate=>candidate.dataset.control===name):null,defaultHeatStops=[[0,[48,18,59]],[.25,[50,104,172]],[.5,[39,186,142]],[.75,[237,225,65]],[1,[180,4,38]]];let heatmapStyleSignature='';
  const selectedHeatStops=()=>{const name=config.overview_colormap_control,input=controlInput(name),control=controls.find(candidate=>candidate.name===name);if(!input||!control)return defaultHeatStops;const index=(control.options||[]).findIndex(option=>String(option)===String(input.value)),entries=(control.option_previews||[])[Math.max(0,index)]||[],parsed=entries.map((entry,entryIndex)=>{const text=String(entry),rgb=text.match(/rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i),hex=text.match(/#([0-9a-f]{6})/i),position=text.match(/([+-]?\d+(?:\.\d+)?)%\s*$/);let color=null;if(rgb)color=rgb.slice(1,4).map(Number);else if(hex)color=[1,3,5].map(offset=>parseInt(hex[1].slice(offset-1,offset+1),16));if(!color||color.some(value=>!Number.isFinite(value)))return null;return[position?Number(position[1])/100:entryIndex/Math.max(1,entries.length-1),color]}).filter(Boolean);return parsed.length>=2?parsed:defaultHeatStops};
  const selectedHeatLimits=values=>{const raw=controlInput(config.overview_limits_control)?.value,parts=String(raw||'').split(',',2).map(Number);if(parts.length===2&&Number.isFinite(parts[0])&&Number.isFinite(parts[1])&&parts[1]>parts[0])return parts;const ordered=[...values].sort((left,right)=>left-right);return[ordered[Math.floor((ordered.length-1)*.02)],ordered[Math.ceil((ordered.length-1)*.995)]]};
  const heatColor=(value,stops)=>{const scaled=Math.min(1,Math.max(0,value));let upper=1;while(upper<stops.length-1&&scaled>stops[upper][0])upper+=1;const lower=Math.max(0,upper-1),span=stops[upper][0]-stops[lower][0]||1,mix=(scaled-stops[lower][0])/span;return stops[lower][1].map((channel,offset)=>Math.round(channel+(stops[upper][1][offset]-channel)*mix))};
  const prepareHeatmap=()=>{const rows=(config.overview_heatmap||[]).map(row=>row.map(Number));if(!rows.length||!rows[0]?.length)return null;const rowCount=rows.length,columnCount=rows[0].length,values=rows.flat().filter(Number.isFinite),[low,high]=selectedHeatLimits(values),span=high-low||1,stops=selectedHeatStops(),signature=JSON.stringify([low,high,stops]);if(heatmapCanvas&&signature===heatmapStyleSignature)return heatmapCanvas;heatmapStyleSignature=signature;heatmapCanvas=document.createElement('canvas');heatmapCanvas.width=columnCount;heatmapCanvas.height=rowCount;const context=heatmapCanvas.getContext('2d'),image=context.createImageData(columnCount,rowCount);rows.forEach((row,rowIndex)=>row.forEach((value,columnIndex)=>{const color=heatColor((value-low)/span,stops),offset=((rowCount-1-rowIndex)*columnCount+columnIndex)*4;image.data[offset]=color[0];image.data[offset+1]=color[1];image.data[offset+2]=color[2];image.data[offset+3]=220}));context.putImageData(image,0,0);return heatmapCanvas};
  const drawOverview=()=>{const rect=canvas.getBoundingClientRect(),ratio=devicePixelRatio||1,width=Math.max(1,Math.round(rect.width*ratio)),height=Math.max(1,Math.round(rect.height*ratio));if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height}const context=canvas.getContext('2d'),values=selectedOverview().map(Number).filter(Number.isFinite),heatmap=prepareHeatmap();context.clearRect(0,0,width,height);if(heatmap){context.imageSmoothingEnabled=false;context.drawImage(heatmap,0,0,width,height)}if(values.length<2)return;const limits=values.reduce((result,value)=>[Math.min(result[0],value),Math.max(result[1],value)],[Infinity,-Infinity]),low=limits[0],span=limits[1]-low||1,style=getComputedStyle(document.documentElement);context.beginPath();values.forEach((value,index)=>{const x=index/(values.length-1)*width,y=height-2-(value-low)/span*(height-4);if(index)context.lineTo(x,y);else context.moveTo(x,y)});context.strokeStyle=heatmap?'rgba(255,255,255,.92)':style.getPropertyValue('--accent').trim();context.lineWidth=Math.max(1,ratio);if(heatmap){context.shadowColor='rgba(0,0,0,.8)';context.shadowBlur=2*ratio}context.stroke();context.shadowBlur=0};
  const render=()=>{clamp();const spec=timelineSpec(config),displayDuration=selectedDuration(),[displayStart,displayEnd]=displayedWindow(),leftPercent=displayDuration?displayStart/displayDuration*100:0,rightPercent=displayDuration?displayEnd/displayDuration*100:100;selection.style.left=`${leftPercent}%`;selection.style.width=`${rightPercent-leftPercent}%`;left.style.left=`${leftPercent}%`;right.style.left=`${rightPercent}%`;startInput.value=timeBoxValue(displayStart,config);endInput.value=timeBoxValue(displayEnd,config);startInput.max=endInput.max=displayTime(displayDuration,config);widthInput.min=displayTime(Math.min(minimum,displayDuration),config);widthInput.max=displayTime(displayDuration,config);startInput.step=endInput.step=widthInput.step=displayTime(step,config);startInput.setAttribute('aria-label',`Window start time in ${spec.label}`);endInput.setAttribute('aria-label',`Window stop time in ${spec.label}`);widthInput.setAttribute('aria-label',`Window width in ${spec.label}`);totalLabel.textContent=`/ ${formatTimelineTime(displayDuration,config)}`;widthInput.value=timeBoxValue(displayEnd-displayStart,config);unitLabel.textContent=`${spec.label} buffer`;left.setAttribute('aria-valuenow',String(displayStart));right.setAttribute('aria-valuenow',String(displayEnd));drawOverview();renderAnnotationMarkers(config)};
  redrawWindowOverview=render;
  const commit=async()=>{if(updating){pending=true;return}updating=true;try{await refresh()}finally{updating=false;if(pending){pending=false;void commit()}}};
  const scheduleCommit=()=>{if(commitTimer!==null)return;commitTimer=setTimeout(()=>{commitTimer=null;void commit()},75)};
  const finalCommit=()=>{if(commitTimer!==null){clearTimeout(commitTimer);commitTimer=null}void commit()};
  fullExtent.onclick=()=>{windowStart=0;windowEnd=selectedDuration();render();finalCommit()};
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
  const root=document.querySelector('#segmented-bar'),track=root?.querySelector('#segmented-track'),previous=root?.querySelector('#segment-previous'),next=root?.querySelector('#segment-next'),toggle=root?.querySelector('#segment-toggle'),rateInput=root?.querySelector('#segment-rate'),stepInput=root?.querySelector('#segment-step'),counter=root?.querySelector('#segment-count'),time=root?.querySelector('#segment-time');if(!root||!track)return;const lifecycle=++segmentedPlaybackGeneration,trackInset=8;clearTimeout(playbackTimer);playbackTimer=null;let updating=false,queuedDirection=0,queuedIdentifier=null,autoPlaying=false,scrubPointer=null,lastScrubIdentifier=null;
  const available=()=>config.segments||[];
  const selectedIndex=()=>Math.max(0,available().findIndex(segment=>segment.identifier===segmentId));
  const stopAuto=()=>{autoPlaying=false;clearTimeout(playbackTimer);playbackTimer=null;if(toggle){toggle.textContent='▶';toggle.setAttribute('aria-label','Play segments');toggle.title='Play segments'}};
  const playbackStep=()=>Math.min(Math.max(1,available().length-1),Math.max(1,Math.floor(Number(stepInput?.value)||1)));
  const segmentExtent=segments=>segments.map(candidate=>Number(candidate.start_seconds)).filter(Number.isFinite).reduce((bounds,value)=>[Math.min(bounds[0],value),Math.max(bounds[1],value)],[Infinity,-Infinity]);
  const segmentFraction=(segment,index,segments,extent)=>{const span=extent[1]-extent[0],start=Number(segment.start_seconds);return span&&Number.isFinite(start)?Math.min(1,Math.max(0,(start-extent[0])/span)):(segments.length>1?index/(segments.length-1):0)};
  const render=()=>{const segments=available();if(!segments.length)return;if(!segments.some(segment=>segment.identifier===segmentId))segmentId=config.selected_segment_id||segments[0].identifier;const index=selectedIndex(),selected=segments[index],duration=Number(config.duration_seconds)||0,unit=timelineSpec(config).label,disabled=segments.length<2,extent=segmentExtent(segments),markers=segments.map((segment,segmentIndex)=>{const percent=segmentFraction(segment,segmentIndex,segments,extent)*100,label=segment.label||segment.identifier,title=`${label} · ${fixedSegmentTime(segment.start_seconds,config)} ${unit} · ${fixedSegmentTime(segment.duration_seconds,config)} ${unit}`;return `<button class="segment-marker ${segment.identifier===segmentId?'active':''}" type="button" style="left:${percent}%" data-segment-id="${esc(segment.identifier)}" aria-label="${esc(title)}" title="${esc(title)}"></button>`}).join('');track.innerHTML=`<div class="annotation-markers" data-annotation-markers></div><div class="segment-marker-layer">${markers}</div>`;counter.textContent=`${index+1} / ${segments.length}`;time.textContent=formatSegmentRange(selected,duration,config);previous.disabled=next.disabled=toggle.disabled=disabled;stepInput.max=String(Math.max(1,segments.length-1));stepInput.value=String(playbackStep());if(autoPlaying&&disabled)stopAuto();track.querySelectorAll('[data-segment-id]').forEach(marker=>marker.onclick=event=>{event.stopPropagation();void select(marker.dataset.segmentId)});renderAnnotationMarkers(config)};
  const select=async identifier=>{if(updating){queuedIdentifier=identifier;return true}if(identifier===segmentId)return false;segmentId=identifier;render();updating=true;try{await refresh();return true}finally{updating=false;render();const pendingIdentifier=queuedIdentifier;queuedIdentifier=null;if(pendingIdentifier&&pendingIdentifier!==segmentId){void select(pendingIdentifier)}else if(queuedDirection){const direction=queuedDirection;queuedDirection=0;void advance(direction)}}};
  const advance=async direction=>{if(updating){queuedDirection=direction;return true}const segments=available();if(segments.length<2)return false;const target=(selectedIndex()+direction*playbackStep()+segments.length)%segments.length;return select(segments[target].identifier)};
  const nearestSegment=clientX=>{const segments=available();if(!segments.length)return null;const bounds=track.getBoundingClientRect(),usableWidth=Math.max(1,bounds.width-trackInset*2),fraction=Math.min(1,Math.max(0,(clientX-bounds.left-trackInset)/usableWidth)),extent=segmentExtent(segments);return segments.reduce((nearest,segment,index)=>Math.abs(segmentFraction(segment,index,segments,extent)-fraction)<Math.abs(segmentFraction(nearest.segment,nearest.index,segments,extent)-fraction)?{segment,index}:nearest,{segment:segments[0],index:0}).segment};
  const scrub=event=>{const nearest=nearestSegment(event.clientX);if(!nearest||nearest.identifier===lastScrubIdentifier)return;lastScrubIdentifier=nearest.identifier;void select(nearest.identifier)};
  const finishScrub=event=>{if(scrubPointer!==event.pointerId)return;scrub(event);scrubPointer=null;lastScrubIdentifier=null;track.classList.remove('scrubbing');if(track.hasPointerCapture?.(event.pointerId))track.releasePointerCapture(event.pointerId)};
  track.onpointerdown=event=>{if(event.button!==0)return;event.preventDefault();stopAuto();queuedDirection=0;scrubPointer=event.pointerId;lastScrubIdentifier=null;track.classList.add('scrubbing');track.setPointerCapture?.(event.pointerId);scrub(event)};
  track.onpointermove=event=>{if(scrubPointer!==event.pointerId)return;event.preventDefault();scrub(event)};
  track.onpointerup=finishScrub;track.onpointercancel=event=>{if(scrubPointer!==event.pointerId)return;scrubPointer=null;lastScrubIdentifier=null;track.classList.remove('scrubbing')};
  const bindHold=(button,direction)=>{let delay=null,repeat=null,suppressClick=false,resetClick=null;const clear=()=>{clearTimeout(delay);clearInterval(repeat);delay=repeat=null;if(queuedDirection===direction)queuedDirection=0;clearTimeout(resetClick);resetClick=setTimeout(()=>{suppressClick=false},0)};button.onpointerdown=event=>{if(button.disabled)return;event.preventDefault();suppressClick=true;button.setPointerCapture?.(event.pointerId);void advance(direction);delay=setTimeout(()=>{repeat=setInterval(()=>void advance(direction),150)},350)};button.onpointerup=clear;button.onpointercancel=()=>{suppressClick=false;clear()};button.onlostpointercapture=clear;button.onclick=()=>{if(suppressClick){suppressClick=false;return}void advance(direction)}};
  const scheduleAuto=()=>{clearTimeout(playbackTimer);if(!autoPlaying||lifecycle!==segmentedPlaybackGeneration)return;const rate=Math.min(30,Math.max(.1,Number(rateInput?.value)||1));rateInput.value=String(rate);playbackTimer=setTimeout(async()=>{const moved=await advance(1);if(!autoPlaying||lifecycle!==segmentedPlaybackGeneration)return;if(!moved){stopAuto();return}scheduleAuto()},1000/rate)};
  const startAuto=()=>{if(next.disabled)return;autoPlaying=true;toggle.textContent='❚❚';toggle.setAttribute('aria-label','Pause segments');toggle.title='Pause segments';scheduleAuto()};
  activeAnnotationSeek=value=>{const position=Number(value)||0,segments=available(),nearest=segments.reduce((best,segment)=>Math.abs(Number(segment.start_seconds)-position)<Math.abs(Number(best.start_seconds)-position)?segment:best,segments[0]);if(nearest)void select(nearest.identifier)};
  bindHold(previous,-1);bindHold(next,1);toggle.onclick=()=>autoPlaying?stopAuto():startAuto();rateInput.onchange=()=>{if(autoPlaying)scheduleAuto();else rateInput.value=String(Math.min(30,Math.max(.1,Number(rateInput.value)||1)))};stepInput.onchange=render;render()
}
function startFrameworkRefresh(config,refresh){let updating=false;playbackTimer=setInterval(async()=>{if(updating)return;updating=true;try{await refresh()}finally{updating=false}},config.interval_seconds*1000)}
const controlLabel=name=>name.split('_').map(x=>x[0].toUpperCase()+x.slice(1)).join(' ');
function controlHtml(control,values){const value=values[control.name]??control.default;if(control.control_type==='toggle')return `<span class="toggle-control"><input type="checkbox" data-control="${esc(control.name)}" ${String(value).toLowerCase()==='true'?'checked':''}><span class="toggle-track"></span></span>`;if(control.control_type==='select')return `<select data-control="${esc(control.name)}">${control.options.map((option,index)=>`<option value="${esc(option)}" ${String(value)===String(option)?'selected':''}>${esc(control.option_labels?.[index]??option)}</option>`).join('')}</select>`;if(control.control_type==='color')return `<input type="color" data-control="${esc(control.name)}" value="${esc(value)}">`;if(control.control_type==='integer'||control.control_type==='float')return `<input type="number" data-control="${esc(control.name)}" value="${esc(value)}" ${control.minimum==null?'':`min="${esc(control.minimum)}"`} ${control.maximum==null?'':`max="${esc(control.maximum)}"`} ${control.step==null?'':`step="${esc(control.step)}"`}>`;return `<input data-control="${esc(control.name)}" value="${esc(value)}">`}
function controlFieldHtml(control,values){return `<label>${esc(control.label||controlLabel(control.name))}${controlHtml(control,values)}</label>`}
function stylePickerHtml(controls,values){const color=controls.find(control=>control.control_type==='color'),value=color?(values[color.name]??color.default):'#60717d',label=controls.find(control=>control.picker_label)?.picker_label||controlLabel(controls[0].picker);return `<details class="style-picker" data-style-picker="${esc(controls[0].picker)}"><summary><span class="style-swatch" data-style-swatch style="background:${esc(value)}"></span><span class="style-picker-name">${esc(label)}</span></summary><div class="style-picker-fields">${controls.map(control=>controlFieldHtml(control,values)).join('')}</div></details>`}
const colormapGradient=colors=>`linear-gradient(90deg,${colors.join(',')})`;
function colormapPickerHtml(control,values){const value=String(values[control.name]??control.default),index=Math.max(0,control.options.findIndex(option=>String(option)===value)),colors=(control.option_previews||[])[index]||[],gradient=colormapGradient(colors);return `<details class="colormap-picker" data-colormap-picker><summary><span class="colormap-preview" data-colormap-preview style="background:${esc(gradient)}"></span><span class="colormap-picker-name">${esc(value)}</span></summary><input type="hidden" data-control="${esc(control.name)}" value="${esc(value)}"><div class="colormap-options">${control.options.map((option,optionIndex)=>{const optionGradient=colormapGradient((control.option_previews||[])[optionIndex]||[]);return `<button class="colormap-option ${String(option)===value?'selected':''}" type="button" data-colormap-option="${esc(option)}" data-colormap-gradient="${esc(optionGradient)}"><span class="colormap-preview" style="background:${esc(optionGradient)}"></span><span>${esc(option)}</span></button>`}).join('')}</div></details>`}
function bindColormapPickers(){document.querySelectorAll('[data-colormap-picker]').forEach(picker=>{const input=picker.querySelector('[data-control]'),preview=picker.querySelector('[data-colormap-preview]'),name=picker.querySelector('.colormap-picker-name');picker.querySelectorAll('[data-colormap-option]').forEach(option=>option.onclick=()=>{input.value=option.dataset.colormapOption;preview.style.background=option.dataset.colormapGradient;name.textContent=option.dataset.colormapOption;picker.querySelectorAll('[data-colormap-option]').forEach(candidate=>candidate.classList.toggle('selected',candidate===option));picker.open=false;input.dispatchEvent(new Event('change',{bubbles:true}))})})}
function limitsValue(control,values){const fallback=control.default.map(Number),raw=values[control.name]??fallback,parts=Array.isArray(raw)?raw:String(raw).split(',',2),minimum=Number(control.minimum),maximum=Number(control.maximum);let lower=Number(parts[0]),upper=Number(parts[1]);if(!Number.isFinite(lower)||!Number.isFinite(upper)||lower>=upper)[lower,upper]=fallback;lower=Math.max(minimum,Math.min(maximum,lower));upper=Math.max(minimum,Math.min(maximum,upper));return lower<upper?[lower,upper]:fallback}
function limitsPickerHtml(control,values){const [lower,upper]=limitsValue(control,values),minimum=Number(control.minimum),maximum=Number(control.maximum),step=Number(control.step)||1;return `<div class="limits-picker" data-limits-picker><div class="limits-picker-head"><span class="limits-picker-name">${esc(control.label||controlLabel(control.name))}</span><input type="number" data-limit-number="lower" value="${lower}" min="${minimum}" max="${maximum}" step="${step}" aria-label="Lower limit"><span class="limits-separator">to</span><input type="number" data-limit-number="upper" value="${upper}" min="${minimum}" max="${maximum}" step="${step}" aria-label="Upper limit"></div><input type="hidden" data-control="${esc(control.name)}" value="${lower},${upper}"></div>`}
function bindLimitsPickers(onCommit){document.querySelectorAll('[data-limits-picker]').forEach(picker=>{const hidden=picker.querySelector('[data-control]'),lowerNumber=picker.querySelector('[data-limit-number="lower"]'),upperNumber=picker.querySelector('[data-limit-number="upper"]'),minimum=Number(lowerNumber.min),maximum=Number(lowerNumber.max),step=Number(lowerNumber.step)||1;const update=(changed,value)=>{let lower=Number(lowerNumber.value),upper=Number(upperNumber.value),next=Number(value);if(!Number.isFinite(next))next=changed==='lower'?lower:upper;if(changed==='lower')lower=Math.min(upper-step,Math.max(minimum,next));else upper=Math.max(lower+step,Math.min(maximum,next));lowerNumber.value=lower;upperNumber.value=upper;hidden.value=`${lower},${upper}`;if(onCommit)void onCommit();else hidden.dispatchEvent(new Event('change',{bubbles:true}))};lowerNumber.onchange=event=>update('lower',event.target.value);upperNumber.onchange=event=>update('upper',event.target.value);lowerNumber.onkeydown=upperNumber.onkeydown=event=>{if(event.key==='Enter')event.currentTarget.blur()}})}
function customControlHtml(control,values){return control.control_type==='colormap'?colormapPickerHtml(control,values):limitsPickerHtml(control,values)}
function controlGroupHtml(controls,values){const special=controls.filter(control=>['colormap','limits'].includes(control.control_type)),regular=controls.filter(control=>!control.picker&&!['colormap','limits'].includes(control.control_type)),pickers=controls.filter(control=>control.picker).reduce((result,control)=>{(result[control.picker]??=[]).push(control);return result},{});const custom=[...special.map(control=>customControlHtml(control,values)),...Object.values(pickers).map(picker=>stylePickerHtml(picker,values))];return `<div class="control-fields">${regular.map(control=>controlFieldHtml(control,values)).join('')}</div>${custom.length?`<div class="style-picker-list">${custom.join('')}</div>`:''}`}
const statText=value=>value!=null&&typeof value==='object'?JSON.stringify(value):String(value??'—');
function statisticsRows(statistics){return Object.entries(statistics||{}).map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(statText(value))}</dd></div>`).join('')}
let currentServerRuntime=0;
function runtimeMilliseconds(value){const parsed=Number.parseFloat(String(value||''));return Number.isFinite(parsed)?parsed:0}
function runtimeRows(statistics){currentServerRuntime=runtimeMilliseconds(statistics?.['Server total']);const preparation=statistics?.['Workspace total']||statistics?.['Analysis runtime']||'—',views=statistics?.['View callbacks']||'—';return `<div><dt>Data & analysis</dt><dd>${esc(preparation)}</dd></div><div><dt>View generation</dt><dd>${esc(views)}</dd></div><div><dt>Browser rendering</dt><dd data-client-stat="browser-runtime">—</dd></div><div class="runtime-total"><dt>Total</dt><dd data-client-stat="total-runtime">—</dd></div>`}
function sidebarHtml(workspaceName,page){const details=page.controls.filter(control=>control.placement!=='inline'),groups=details.reduce((result,control)=>{const label=control.group||'Analysis settings';(result[label]??=[]).push(control);return result},{}),settings=Object.entries(groups).map(([label,controls])=>`<details class="settings-group" open><summary>${esc(label)}</summary><div class="settings-group-body">${controlGroupHtml(controls,page.control_values)}</div></details>`).join(''),crumb=singleWorkspaceMode?`<button id="back">${esc(workspaceName)}</button>`:`<button id="home">Workspaces</button> / <button id="back">${esc(workspaceName)}</button>`;return `<button class="sidebar-backdrop" data-sidebar-backdrop aria-label="Close details"></button><aside class="workspace-sidebar" data-workspace-sidebar aria-label="Workspace details"><div class="sidebar-head"><div class="sidebar-title"><div class="crumb">${crumb}</div><h1>${esc(page.title)}</h1><span class="subtitle">${esc(page.subtitle||'')}</span></div><button class="sidebar-close" data-sidebar-close aria-label="Close details">Close</button></div><div class="analysis-panel">${settings}<section><h2>View details</h2><dl class="view-stats" id="view-stats">${statisticsRows(page.statistics)}</dl></section><section><h2>Runtime</h2><dl class="view-stats" id="runtime-stats">${runtimeRows(page.runtime_statistics)}</dl></section></div></aside>`}
function updateStatistics(statistics,runtimeStatistics){const viewTarget=document.querySelector('#view-stats'),runtimeTarget=document.querySelector('#runtime-stats');if(viewTarget)viewTarget.innerHTML=statisticsRows(statistics);if(runtimeTarget)runtimeTarget.innerHTML=runtimeRows(runtimeStatistics)}
function bindSidebar(){const sidebar=document.querySelector('[data-workspace-sidebar]'),backdrop=document.querySelector('[data-sidebar-backdrop]'),toggle=document.querySelector('[data-sidebar-toggle]');if(!sidebar||!backdrop||!toggle)return;const setOpen=open=>{sidebar.classList.toggle('open',open);backdrop.classList.toggle('open',open);toggle.setAttribute('aria-expanded',String(open))};toggle.onclick=()=>setOpen(!sidebar.classList.contains('open'));backdrop.onclick=()=>setOpen(false);sidebar.querySelector('[data-sidebar-close]').onclick=()=>setOpen(false)}
const batchState=action=>action?.status||'idle';
const batchStatusGlyph=action=>({running:'●',pending:'●',cancelling:'●',ready:'✓',error:'!'})[batchState(action)]||'';
const batchActionStateLabel=action=>{const state=batchState(action),glyph=batchStatusGlyph(action);return state==='ready'?'↻ rerun':glyph?`${glyph} ${state}`:state};
const batchLauncherHtml=action=>`<span class="batch-play" aria-hidden="true">▶</span>`;
const batchFolderIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 6.5h6l2 2h9v9.5a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18z"/><path d="M3.5 9h17"/></svg>';
const batchBrowseAction=batch=>batch?.actions?.find(action=>activeBatchStatuses.has(batchState(action))&&action.result_browser_url)||batch?.actions?.find(action=>batchState(action)==='ready'&&action.result_browser_url)||batch?.actions?.find(action=>action.result_browser_url);
function batchFolderHtml(batch){const action=batchBrowseAction(batch),url=action?.result_browser_url,label=action?`Browse results for ${action.label}`:'No batch results yet',content=`${batchFolderIcon}<span class="batch-folder-label">Browse</span>`;return url?`<a class="batch-folder" data-batch-folder href="${esc(url)}" aria-label="${esc(label)}" title="${esc(label)}">${content}</a>`:`<span class="batch-folder" data-batch-folder aria-disabled="true" aria-label="${esc(label)}" title="${esc(label)}">${content}</span>`}
function batchArtifactHtml(file){const action=file.browse_url?`<a class="batch-open" href="${esc(file.browse_url)}">Browse</a>`:file.open_url?`<a class="batch-open" href="${esc(file.open_url)}" target="_blank" rel="noopener">Open</a>`:`<a class="batch-open" href="${esc(file.download_url||file.url)}">Download</a>`;return `<div class="batch-artifact"><span class="batch-path" title="${esc(file.path)}">${esc(file.path)}</span>${action}<button class="copy-path" type="button" data-copy-path="${esc(file.path)}">Copy path</button></div>`}
function batchMenuHtml(batch,url,showArtifacts=true){if(!batch?.enabled)return '';const summary=batch.actions.find(action=>['running','pending','cancelling'].includes(batchState(action)))||batch.actions.find(action=>batchState(action)==='ready')||batch.actions.find(action=>batchState(action)==='error')||batch.actions[0];return `<details class="batch-menu ${esc(batchState(summary))}" data-batch-menu data-batch-url="${esc(url)}"><summary title="Run batch action" aria-label="Run batch action">${batchLauncherHtml(summary)}</summary><div class="batch-menu-popover">${batch.actions.map(action=>`<div class="batch-action-row"><button class="batch-action" type="button" title="${batchState(action)==='ready'?'Regenerate existing result':'Run batch action'}" data-batch-action="${esc(action.value)}" data-batch-status="${esc(batchState(action))}"><span>${esc(action.label)}</span><span class="batch-state ${esc(batchState(action))}">${esc(batchActionStateLabel(action))}</span></button>${showArtifacts&&action.files?.length?`<div class="batch-artifacts">${action.files.map(batchArtifactHtml).join('')}</div>`:''}</div>`).join('')}</div></details>`}
function batchControlsHtml(batch,url){if(!batch?.enabled)return '<span class="batch-controls"></span>';return `<span class="batch-controls" data-batch-controls>${batchMenuHtml(batch,url,false)}${batchFolderHtml(batch)}</span>`}
function bindItemActionRail(){itemActionResizeObserver?.disconnect();const layout=document.querySelector('[data-item-browser-layout]');if(!layout)return;const table=layout.querySelector('table'),head=table?.querySelector('thead tr'),railHead=layout.querySelector('.item-action-rail-head'),rows=[...(table?.querySelectorAll('tbody tr')||[])],actions=[...layout.querySelectorAll('.item-action-row')],sync=()=>{if(head&&railHead)railHead.style.height=`${head.getBoundingClientRect().height}px`;rows.forEach((row,index)=>{if(actions[index])actions[index].style.height=`${row.getBoundingClientRect().height}px`})};sync();itemActionResizeObserver=new ResizeObserver(sync);itemActionResizeObserver.observe(table)}
async function copyText(value){if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(value);return}const input=document.createElement('textarea');input.value=value;input.style.position='fixed';input.style.opacity='0';document.body.append(input);input.select();const copied=document.execCommand('copy');input.remove();if(!copied)throw new Error('Clipboard access is unavailable')}
function bindCopyPaths(root=document){root.querySelectorAll('[data-copy-path]').forEach(button=>button.onclick=async event=>{event.preventDefault();event.stopPropagation();const label=button.textContent;try{await copyText(button.dataset.copyPath);button.textContent='Copied'}catch(error){button.textContent='Copy failed'}finally{setTimeout(()=>button.textContent=label,1200)}})}
const notifications=[],batchPollers=new Map(),batchDismissedKey='sigvue-dismissed-batches',batchAlertedKey='sigvue-alerted-batches';
function storedBatchIds(key){try{return new Set(JSON.parse(sessionStorage.getItem(key)||'[]'))}catch(error){return new Set()}}
const dismissedBatchIds=storedBatchIds(batchDismissedKey),alertedBatchIds=storedBatchIds(batchAlertedKey);
function storeBatchIds(key,values){try{sessionStorage.setItem(key,JSON.stringify([...values]))}catch(error){}}
const activeBatchStatuses=new Set(['pending','running','cancelling']);
function batchNotificationTitle(status){const label=status.action_label||'Batch action';return `${label} ${{pending:'queued',running:'running',cancelling:'cancelling',cancelled:'cancelled',ready:'complete',error:'failed'}[status.status]||status.status}`}
function batchNotificationContext(status){return [status.workspace_name,status.item_title].filter(Boolean).join(' · ')}
function batchProgressHtml(notification){const progress=notification.progress,total=Number(progress?.total||0);if(total<=0)return '';const completed=Math.max(0,Math.min(total,Number(progress.completed||0))),succeeded=Math.max(0,Math.min(total,Number(progress.succeeded||0))),failed=Math.max(0,Math.min(total-succeeded,Number(progress.failed||0))),successPercent=100*succeeded/total,failedPercent=100*failed/total,errors=(progress.items||[]).filter(item=>item.status==='error').map(item=>{const detail=item.detail||'',log=item.log||'';return `<div class="notification-progress-item error"><div class="notification-progress-row"><span class="notification-progress-state">Error</span><span class="notification-progress-name" title="${esc(item.title||item.id)}">${esc(item.title||item.id)}</span></div>${detail||log?`<details class="notification-progress-log"><summary>${esc(detail||'View error log')}</summary>${log?`<pre>${esc(log)}</pre>`:''}</details>`:''}</div>`}).join(''),active=activeBatchStatuses.has(notification.status);return `<div class="notification-progress">${errors?`<div class="notification-progress-errors">${errors}</div>`:''}<div class="notification-progress-head"><span>${completed} of ${total} processed${failed?` · ${failed} failed`:''}</span>${active?`<button class="notification-cancel" type="button" data-cancel-batch="${esc(notification.id)}" ${notification.status==='cancelling'?'disabled':''}>${notification.status==='cancelling'?'Cancelling…':'Cancel'}</button>`:''}</div><div class="notification-progress-track" role="progressbar" aria-label="Batch progress" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${completed}"><div class="notification-progress-success" style="width:${successPercent}%"></div><div class="notification-progress-failed" style="width:${failedPercent}%"></div></div></div>`}
function batchErrorHtml(notification){if(notification.status!=='error'||!notification.log)return '';return `<details class="notification-progress-log notification-job-log"><summary>View error log</summary><pre>${esc(notification.log)}</pre></details>`}
function batchOutputHtml(notification){const files=notification.files||[],active=activeBatchStatuses.has(notification.status);if(active&&notification.result_browser_url)return `<div class="notification-files"><div class="batch-artifact"><span class="batch-path" title="${esc(notification.output_directory||'')}">${files.length?`${files.length} finished output${files.length===1?'':'s'}`:'Finished outputs will appear here'}</span><a class="batch-open" href="${esc(notification.result_browser_url)}">Browse live results</a></div></div>`;if(!files.length)return '';if(files.length===1)return `<div class="notification-files">${files.map(batchArtifactHtml).join('')}</div>`;return `<div class="notification-files"><div class="batch-artifact"><span class="batch-path" title="${esc(notification.output_directory||'')}">${files.length} outputs${notification.output_directory?` · ${esc(notification.output_directory)}`:''}</span>${notification.result_browser_url?`<a class="batch-open" href="${esc(notification.result_browser_url)}">Browse results</a>`:''}${notification.output_directory?`<button class="copy-path" type="button" data-copy-path="${esc(notification.output_directory)}">Copy folder</button>`:''}</div></div>`}
function renderNotifications(){notificationBadge.hidden=!notifications.length;notificationBadge.textContent=String(notifications.length);notificationList.innerHTML=notifications.length?notifications.map(notification=>{const context=batchNotificationContext(notification),message=notification.summary||notification.detail||(!context?'Working in the background.':'');return `<article class="notification-item" data-notification="${esc(notification.notificationId)}"><div class="notification-title"><span class="notification-status ${esc(notification.status)}">${esc(notification.status)}</span><strong>${esc(batchNotificationTitle(notification))}</strong><button class="notification-dismiss" type="button" data-dismiss-notification="${esc(notification.notificationId)}" aria-label="Dismiss">×</button></div>${message?`<p class="notification-summary">${esc(message)}</p>`:''}${context?`<p class="notification-context">${esc(context)}</p>`:''}${batchErrorHtml(notification)}${batchOutputHtml(notification)}${batchProgressHtml(notification)}</article>`}).join(''):'<p class="notification-empty">No notifications yet.</p>';bindCopyPaths(notificationList);notificationList.querySelectorAll('[data-dismiss-notification]').forEach(button=>button.onclick=event=>{event.preventDefault();event.stopPropagation();const index=notifications.findIndex(item=>item.notificationId===button.dataset.dismissNotification);if(index>=0){const [removed]=notifications.splice(index,1);if(removed.id){dismissedBatchIds.add(removed.id);storeBatchIds(batchDismissedKey,dismissedBatchIds)}}renderNotifications()});notificationList.querySelectorAll('[data-cancel-batch]').forEach(button=>button.onclick=async event=>{event.preventDefault();event.stopPropagation();button.disabled=true;button.textContent='Cancelling…';try{monitorBatchJob(await apiPost(`/batches/${encodeURIComponent(button.dataset.cancelBatch)}/cancel`,{}))}catch(error){button.disabled=false;button.textContent='Cancel';alert(`Unable to cancel batch: ${error.message}`)}})}
function showBatchToast(status){if(!status.id||alertedBatchIds.has(status.id)||dismissedBatchIds.has(status.id))return;alertedBatchIds.add(status.id);storeBatchIds(batchAlertedKey,alertedBatchIds);const toast=document.createElement('div');toast.className=`notification-toast ${status.status==='error'?'error':''}`;toast.innerHTML=`<strong>${esc(batchNotificationTitle(status))}</strong><span>${esc(status.summary||status.detail||batchNotificationContext(status))}</span>`;notificationToasts.append(toast);setTimeout(()=>toast.remove(),3000)}
function batchNotificationIdentity(status){return `${status.workspace_id||''}\u0000${status.item_id||''}\u0000${status.action||''}`}
function upsertBatchNotification(status){if(!status?.id||dismissedBatchIds.has(status.id))return;const notificationId=`batch-${status.id}`,identity=batchNotificationIdentity(status),index=notifications.findIndex(item=>item.notificationId===notificationId),replaced=index<0?notifications.findIndex(item=>batchNotificationIdentity(item)===identity):-1;if(index>=0){const previous=notifications[index],entry={...previous,...status,notificationId};notifications[index]=entry;if(previous.status!==entry.status){notifications.splice(index,1);notifications.unshift(entry)}}else{if(replaced>=0)notifications.splice(replaced,1);notifications.unshift({...status,notificationId})}renderNotifications()}
function batchStatusUrl(status){return status.item_id?`/workspaces/${encodeURIComponent(status.workspace_id)}/items/${encodeURIComponent(status.item_id)}/batch`:`/workspaces/${encodeURIComponent(status.workspace_id)}/batch`}
function setBatchMenuActionStatus(menu,action,visibleStatus){const button=[...menu.querySelectorAll('[data-batch-action]')].find(candidate=>candidate.dataset.batchAction===action);if(!button)return false;button.dataset.batchStatus=visibleStatus;button.title=visibleStatus==='ready'?'Regenerate existing result':'Run batch action';const state=button.querySelector('.batch-state');state.className=`batch-state ${visibleStatus}`;state.textContent=batchActionStateLabel({status:visibleStatus});const buttons=[...menu.querySelectorAll('[data-batch-action]')],summary=buttons.find(candidate=>['running','pending','cancelling'].includes(candidate.dataset.batchStatus))||buttons.find(candidate=>candidate.dataset.batchStatus==='ready')||buttons.find(candidate=>candidate.dataset.batchStatus==='error')||buttons[0],menuStatus=summary?.dataset.batchStatus||'idle';menu.className=`batch-menu ${menuStatus}`;menu.querySelector('summary').innerHTML=batchLauncherHtml({status:menuStatus});return true}
function setBatchFolderStatus(menu,status){const controls=menu.closest('[data-batch-controls]'),folder=controls?.querySelector('[data-batch-folder]');if(!folder)return;const label=status.result_browser_url?`Browse results for ${status.action_label||'batch action'}`:'No current batch results yet',content=`${batchFolderIcon}<span class="batch-folder-label">Browse</span>`;folder.outerHTML=status.result_browser_url?`<a class="batch-folder" data-batch-folder href="${esc(status.result_browser_url)}" aria-label="${esc(label)}" title="${esc(label)}">${content}</a>`:`<span class="batch-folder" data-batch-folder aria-disabled="true" aria-label="${esc(label)}" title="${esc(label)}">${content}</span>`}
function applyWorkspaceItemProgress(status){if(status.item_id!=null)return;const prefix=`/workspaces/${encodeURIComponent(status.workspace_id)}/items/`,suffix='/batch',progress=new Map((status.progress?.items||[]).map(item=>[String(item.id),item])),active=activeBatchStatuses.has(status.status);document.querySelectorAll('[data-batch-menu]').forEach(menu=>{const url=menu.dataset.batchUrl||'';if(!url.startsWith(prefix)||!url.endsWith(suffix))return;let itemId;try{itemId=decodeURIComponent(url.slice(prefix.length,-suffix.length))}catch(error){return}const item=progress.get(itemId),itemState=item?.status,visibleStatus=itemState==='ready'?'ready':itemState==='error'?'error':itemState==='running'&&active?(status.status==='cancelling'?'cancelling':'running'):'idle';if(!setBatchMenuActionStatus(menu,status.action,visibleStatus))return;setBatchFolderStatus(menu,{...status,item_id:itemId,status:visibleStatus,result_browser_url:visibleStatus==='ready'?status.result_browser_url:null})})}
function applyVisibleBatchStatus(status){if(!status.workspace_id)return;const url=batchStatusUrl(status);document.querySelectorAll('[data-batch-menu]').forEach(menu=>{if(menu.dataset.batchUrl!==url)return;const visibleStatus=status.status==='cancelled'?'idle':status.status;if(!setBatchMenuActionStatus(menu,status.action,visibleStatus))return;setBatchFolderStatus(menu,status)});applyWorkspaceItemProgress(status)}
function monitorBatchJob(started){if(!started?.id)return;upsertBatchNotification(started);applyVisibleBatchStatus(started);if(!activeBatchStatuses.has(started.status)){showBatchToast(started);return}if(batchPollers.has(started.id))return;const poll=async()=>{try{const status=await api(started.status_url||`/batches/${encodeURIComponent(started.id)}`);upsertBatchNotification(status);applyVisibleBatchStatus(status);if(activeBatchStatuses.has(status.status)){batchPollers.set(status.id,setTimeout(poll,500));return}batchPollers.delete(status.id);showBatchToast(status)}catch(error){batchPollers.delete(started.id);const failed={...started,status:'error',detail:error.message};upsertBatchNotification(failed);applyVisibleBatchStatus(failed);showBatchToast(failed)}};batchPollers.set(started.id,setTimeout(poll,500))}
async function syncBatchNotifications(){try{const response=await api('/batches');for(const status of [...(response.jobs||[])].reverse())monitorBatchJob(status)}catch(error){console.error('Unable to restore batch notifications',error)}}
function closeBatchMenus(except=null){document.querySelectorAll('[data-batch-menu][open]').forEach(menu=>{if(menu!==except)menu.open=false})}
headerNotifications.onclick=event=>{event.stopPropagation();closeBatchMenus()};
document.addEventListener('click',()=>{closeBatchMenus();headerNotifications.open=false});
function bindBatchMenus(){document.querySelectorAll('[data-batch-menu]').forEach(menu=>{menu.onclick=event=>{event.stopPropagation();headerNotifications.open=false;if(event.target.closest('summary'))closeBatchMenus(menu)};bindCopyPaths(menu);menu.querySelectorAll('[data-batch-action]').forEach(button=>button.onclick=async event=>{event.preventDefault();event.stopPropagation();button.dataset.batchStatus='running';const state=button.querySelector('.batch-state');state.className='batch-state running';state.textContent='● running';menu.className='batch-menu running';menu.querySelector('summary').innerHTML=batchLauncherHtml({status:'running'});menu.open=false;try{monitorBatchJob(await apiPost(menu.dataset.batchUrl,{action:button.dataset.batchAction}))}catch(error){button.dataset.batchStatus='error';state.className='batch-state error';state.textContent='! error';menu.className='batch-menu error';const failed={id:`client-${Date.now()}-${Math.random()}`,action:button.dataset.batchAction,action_label:button.querySelector('span')?.textContent||'Batch action',status:'error',detail:error.message};upsertBatchNotification(failed);showBatchToast(failed)}})})}
function resultSize(value){const size=Number(value);if(!Number.isFinite(size)||size<0)return '';if(size<1024)return`${size} B`;const units=['KB','MB','GB','TB'];let amount=size/1024,index=0;while(amount>=1024&&index<units.length-1){amount/=1024;index++}return`${amount>=10?amount.toFixed(0):amount.toFixed(1)} ${units[index]}`}
function sizeResultBrowser(){cancelAnimationFrame(resultBrowserResizeFrame);resultBrowserResizeFrame=requestAnimationFrame(()=>{const target=document.querySelector('.result-browser');if(!target)return;const available=Math.max(384,Math.floor(window.innerHeight-target.getBoundingClientRect().top+46));target.style.height=`${available}px`})}
async function resultBrowser(scope,identifier,root,navigate=true,directory=[]){
  stopPlayback();const generation=resultBrowserGeneration;activeThemeRefresh=null;workspaceAdd.hidden=true;headerDetails.hidden=true;headerDownload.hidden=true;headerDownload.open=false;headerAnnotate.hidden=true;headerAnnotate.open=false;app.className='result-page';
  const route=root?`/results/${[scope,identifier,root,...directory].map(encodeURIComponent).join('/')}`:`/results/${[scope,identifier].map(encodeURIComponent).join('/')}${directory.length?`?${new URLSearchParams({path:directory.join('/')})}`:''}`;if(navigate)pushRoute(route);
  try{
    const browserParts=[scope,identifier,...(root?[root]:[])],treeKey=parts=>JSON.stringify(parts),endpointFor=parts=>`/batch-browser/${browserParts.map(encodeURIComponent).join('/')}?${new URLSearchParams({path:[...directory,...parts].join('/')})}`,crumbs=directory.map((name,index)=>` / <button type="button" data-result-level="${index+1}">${esc(name)}</button>`).join(''),rootLabel=root||'Batch results',treeListings=new Map(),expanded=new Set(),loading=new Set(),treeErrors=new Map();let listing=await api(endpointFor([]));if(generation!==resultBrowserGeneration)return;treeListings.set(treeKey([]),listing);let selectedKey=null,selectedVersion=null,refreshing=false,renderedNodes=[];
    app.innerHTML=`<div class="crumb"><button type="button" id="result-close">Back</button> / <button type="button" data-result-level="0">${esc(rootLabel)}</button>${crumbs}</div><h1>${esc(directory.at(-1)||rootLabel)}</h1><p class="lead" id="result-summary"></p><div class="toolbar"><input id="result-search" type="search" placeholder="Search loaded files and folders…"><button class="copy-path" type="button" data-copy-path="${esc(listing.path)}">Copy folder path</button></div><div class="result-browser"><div class="result-browser-list" id="result-browser-list" role="tree"></div><div class="result-preview" id="result-preview"><div class="result-empty-preview">Select an image or file to inspect it.</div></div></div>`;
    const list=document.querySelector('#result-browser-list'),preview=document.querySelector('#result-preview'),search=document.querySelector('#result-search'),summary=document.querySelector('#result-summary');
    sizeResultBrowser();window.addEventListener('resize',sizeResultBrowser);
    const flatten=()=>{const nodes=[];const walk=(path,depth)=>{const key=treeKey(path),current=treeListings.get(key);for(const entry of current?.entries||[]){const childPath=[...path,entry.name],childKey=treeKey(childPath),node={...entry,_treePath:childPath,_treeKey:childKey,_depth:depth};nodes.push(node);if(entry.kind!=='directory'||!expanded.has(childKey))continue;if(loading.has(childKey))nodes.push({_message:'Loading folder…',_treeKey:`${childKey}:loading`,_depth:depth+1});else if(treeErrors.has(childKey))nodes.push({_message:treeErrors.get(childKey),_error:true,_treeKey:`${childKey}:error`,_depth:depth+1});else walk(childPath,depth+1)}};walk([],0);return nodes};
    const visible=()=>{const q=search.value.toLowerCase().trim();return flatten().filter(node=>node._message||!q||node.kind==='directory'||node.name.toLowerCase().includes(q))};
    const imageEntries=()=>renderedNodes.filter(entry=>entry.kind==='image');
    const versionedUrl=entry=>`${entry.url}${entry.url.includes('?')?'&':'?'}v=${encodeURIComponent(entry.version||entry.size||0)}`;
    const show=entry=>{
      selectedKey=entry._treeKey;selectedVersion=`${entry._treeKey}:${entry.version||entry.size||0}`;list.querySelectorAll('[data-result-entry]').forEach(row=>row.classList.toggle('active',row.dataset.resultEntry===entry._treeKey));
      if(entry.kind==='image'){
        const images=imageEntries(),index=images.findIndex(candidate=>candidate._treeKey===entry._treeKey),previous=images[(index-1+images.length)%images.length],next=images[(index+1)%images.length];
        preview.innerHTML=`<div class="result-preview-toolbar"><button type="button" data-result-previous ${images.length<2?'disabled':''}>‹</button><button type="button" data-result-next ${images.length<2?'disabled':''}>›</button><strong title="${esc(entry.name)}">${esc(entry.name)}</strong><a href="${esc(entry.url)}" target="_blank" rel="noopener">Open</a><a href="${esc(entry.download_url)}">Download</a><button class="copy-path" type="button" data-copy-path="${esc(entry.path)}">Copy path</button></div><div class="result-image-stage"><img src="${esc(versionedUrl(entry))}" alt="${esc(entry.name)}"><div class="result-empty-preview" data-result-image-message hidden>This image is still being finalized. It will retry automatically.</div></div>`;
        preview.querySelector('[data-result-previous]').onclick=()=>show(previous);preview.querySelector('[data-result-next]').onclick=()=>show(next);const image=preview.querySelector('img'),message=preview.querySelector('[data-result-image-message]');image.onload=()=>{image.hidden=false;message.hidden=true};image.onerror=()=>{image.hidden=true;message.hidden=false;selectedVersion=null}
      }else{
        preview.innerHTML=`<div class="result-file-preview"><strong>${esc(entry.name)}</strong><p>${esc(entry.path)}</p><div class="result-file-actions">${entry.open_url?`<a href="${esc(entry.open_url)}" target="_blank" rel="noopener">Open</a>`:''}<a href="${esc(entry.download_url)}">Download</a><button class="copy-path" type="button" data-copy-path="${esc(entry.path)}">Copy path</button></div></div>`
      }
      bindCopyPaths(preview)
    };
    const loadFolder=async path=>{const key=treeKey(path);loading.add(key);treeErrors.delete(key);draw();try{const child=await api(endpointFor(path));if(generation!==resultBrowserGeneration)return;treeListings.set(key,child)}catch(error){treeErrors.set(key,error.message)}finally{loading.delete(key);if(generation===resultBrowserGeneration)draw()}};
    const toggleFolder=entry=>{const key=entry._treeKey;if(expanded.has(key)){expanded.delete(key);draw();return}expanded.add(key);if(treeListings.has(key)){draw();return}void loadFolder(entry._treePath)};
    const draw=()=>{
      const scrollTop=list.scrollTop,shown=visible(),entries=shown.filter(node=>!node._message),active=activeBatchStatuses.has(listing.status),empty=active?'No finished results yet. This view updates as files complete.':'No matching entries.';renderedNodes=entries;summary.textContent=`${entries.length} loaded entr${entries.length===1?'y':'ies'}${active?' · Batch running; this view updates automatically.':' · Expand folders or inspect generated files.'}`;list.innerHTML=shown.length?shown.map(entry=>entry._message?`<div class="result-tree-message ${entry._error?'error':''}" style="--result-indent:${entry._depth*18}px">${esc(entry._message)}</div>`:`<button class="result-entry ${selectedKey===entry._treeKey?'active':''} ${loading.has(entry._treeKey)?'loading':''}" style="--result-indent:${entry._depth*18}px" type="button" role="treeitem" data-result-entry="${esc(entry._treeKey)}" ${entry.kind==='directory'?`aria-expanded="${expanded.has(entry._treeKey)}"`:''}><span class="result-entry-icon" aria-hidden="true">${entry.kind==='directory'?'<span class="result-disclosure">›</span>':entry.kind==='image'?'▧':'•'}</span><span class="result-entry-name" title="${esc(entry.name)}">${esc(entry.name)}</span><span class="result-entry-size">${entry.kind==='directory'?'':esc(resultSize(entry.size))}</span></button>`).join(''):`<div class="empty">${empty}</div>`;
      list.querySelectorAll('[data-result-entry]').forEach(row=>{const entry=entries.find(candidate=>candidate._treeKey===row.dataset.resultEntry);row.onclick=()=>entry.kind==='directory'?toggleFolder(entry):show(entry)});
      list.scrollTop=scrollTop;const selected=selectedKey?entries.find(entry=>entry._treeKey===selectedKey):null,version=selected?`${selected._treeKey}:${selected.version||selected.size||0}`:null,images=imageEntries();if(selected&&version!==selectedVersion)show(selected);else if(selectedKey&&!selected){selectedKey=null;selectedVersion=null;preview.innerHTML='<div class="result-empty-preview">Select an image or file to inspect it.</div>'}if(!selectedKey&&images.length)show(images[0])
    };
    const refresh=async()=>{if(refreshing||generation!==resultBrowserGeneration)return;refreshing=true;try{listing=await api(endpointFor([]));if(generation!==resultBrowserGeneration)return;treeListings.set(treeKey([]),listing);await Promise.all([...expanded].map(async key=>{try{const path=JSON.parse(key),child=await api(endpointFor(path));if(generation===resultBrowserGeneration){treeListings.set(key,child);treeErrors.delete(key)}}catch(error){treeErrors.set(key,error.message)}}));if(generation!==resultBrowserGeneration)return;draw()}catch(error){summary.textContent=`Live refresh paused: ${error.message}. Retrying…`}finally{refreshing=false}if(generation===resultBrowserGeneration&&!listing.complete)resultBrowserTimer=setTimeout(refresh,750)};
    search.oninput=draw;draw();if(!listing.complete)resultBrowserTimer=setTimeout(refresh,750);bindCopyPaths(app);document.querySelector('#result-close').onclick=()=>history.back();document.querySelectorAll('[data-result-level]').forEach(button=>button.onclick=()=>resultBrowser(scope,identifier,root,true,directory.slice(0,Number(button.dataset.resultLevel))))
  }catch(error){fail(error)}
}
async function catalog(navigate=true){
  stopPlayback();activeThemeRefresh=null;workspaceAdd.hidden=true;headerDetails.hidden=true;headerDownload.hidden=true;headerDownload.open=false;headerAnnotate.hidden=true;headerAnnotate.open=false;app.className='';if(navigate)pushRoute('/');
  try{
    const initial=await api('/workspaces'),workspaces=initial.workspaces;
    singleWorkspaceMode=workspaces.length===1;
    if(singleWorkspaceMode){const workspace=workspaces[0];return items(workspace.id,workspace.name,false,[])}
    workspaceAdd.hidden=false;
    app.innerHTML=`<h1>Workspaces</h1><p class="lead">Choose a workspace, or run one of its batch actions in the background.</p><div class="toolbar"><input id="workspace-search" type="search" placeholder="Search workspaces…" aria-label="Search workspaces"></div><div class="list" id="workspaces"></div>`;
    const draw=()=>{
      const q=document.querySelector('#workspace-search').value.toLowerCase().trim(),shown=workspaces.filter(w=>!q||`${w.name} ${w.description||''} ${w.category||''} ${(w.tags||[]).join(' ')} ${w.id}`.toLowerCase().includes(q)),empty=!workspaces.length&&!q?'<div class="empty">No workspaces are open.<br><button class="primary workspace-empty-action" id="workspace-empty-add" type="button">Add a workspace</button></div>':'<div class="empty">No matching workspaces.</div>';
      document.querySelector('#workspaces').innerHTML=shown.length?shown.map(w=>`<article class="card" data-id="${esc(w.id)}"><div><span class="tag">${esc(w.category||'workspace')}</span><h2>${esc(w.name)}</h2></div><p class="muted">${esc(w.description)}</p><div class="card-tags">${w.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div>${batchMenuHtml(w.batch,`/workspaces/${encodeURIComponent(w.id)}/batch`,false)}</article>`).join(''):empty;
      document.querySelectorAll('[data-id]').forEach(x=>x.onclick=()=>items(x.dataset.id,workspaces.find(w=>w.id===x.dataset.id).name));document.querySelector('#workspace-empty-add')?.addEventListener('click',openWorkspaceWizard);bindBatchMenus()
    };
    draw();document.querySelector('#workspace-search').oninput=draw
  }catch(e){fail(e)}
}
function siDiscoveryValue(value,unit){const number=Number(value);if(!Number.isFinite(number))return String(value);const magnitude=Math.abs(number),prefixes=[[1e12,'T'],[1e9,'G'],[1e6,'M'],[1e3,'k'],[1,''],[1e-3,'m'],[1e-6,'µ'],[1e-9,'n']];const [scale,prefix]=prefixes.find(([scale])=>magnitude>=scale)||[1,''];return `${Number((number/scale).toPrecision(4))} ${prefix}${unit||''}`.trim()}
function discoveryValue(value,column){if(value==null||value==='')return '<span class="discovery-null">—</span>';if(column.kind==='datetime'){const date=new Date(value);return esc(Number.isNaN(date.valueOf())?value:date.toLocaleString())}if(column.kind==='si')return esc(siDiscoveryValue(value,column.unit));return esc(value)}
function discoverySortValue(item,key,kind){const value=key==='title'?item.title:item.summary_fields?.[key];if(value==null||value==='')return null;if(['number','si'].includes(kind))return Number(value);if(kind==='datetime')return new Date(value).valueOf();return String(value).toLocaleLowerCase()}
async function items(id,name,navigate=true,directory=[]){
  stopPlayback();activeThemeRefresh=null;workspaceAdd.hidden=true;headerDetails.hidden=true;headerDownload.hidden=true;headerDownload.open=false;headerAnnotate.hidden=true;headerAnnotate.open=false;app.className='';
  const route=directory.length?`/workspace/${encodeURIComponent(id)}/browse/${directory.map(encodeURIComponent).join('/')}`:(singleWorkspaceMode?'/':`/workspace/${encodeURIComponent(id)}`);if(navigate)history.pushState(null,'',route);
  try{
    const params=new URLSearchParams();directory.forEach(segment=>params.append('directory',segment));
    const listing=await api(`/workspaces/${encodeURIComponent(id)}/items?${params}`),list=listing.items,folders=listing.directories,columns=listing.columns||[],workspaceBatchUrl=`/workspaces/${encodeURIComponent(id)}/batch`,crumbs=directory.map((segment,index)=>` / <button data-directory-level="${index+1}">${esc(segment)}</button>`).join(''),rootCrumb=singleWorkspaceMode?`<button id="workspace-root">${esc(name)}</button>`:`<button id="home">Workspaces</button> / <button id="workspace-root">${esc(name)}</button>`;
    let sortKey='title',sortDescending=false;
    app.innerHTML=`<div class="crumb">${rootCrumb}${crumbs}</div><h1>${esc(directory.at(-1)||name)}</h1><p class="lead">Browse items or dispatch their batch actions without opening them.</p><div class="toolbar item-toolbar"><input id="search" type="search" placeholder="Search this folder…">${batchControlsHtml(listing.batch,workspaceBatchUrl)}</div><div id="items"></div>`;
    const draw=()=>{
      const q=document.querySelector('#search').value.toLowerCase().trim(),shownFolders=folders.filter(folder=>!q||folder.name.toLowerCase().includes(q)),matching=list.filter(item=>!q||`${item.title} ${item.subtitle||''} ${(item.tags||[]).join(' ')} ${item.source_reference||''} ${Object.values(item.summary_fields||{}).filter(value=>value!=null).join(' ')}`.toLowerCase().includes(q)),sortColumn=columns.find(column=>column.key===sortKey),kind=sortColumn?.kind||'text',shown=[...matching].sort((left,right)=>{const a=discoverySortValue(left,sortKey,kind),b=discoverySortValue(right,sortKey,kind);if(a==null)return b==null?0:1;if(b==null)return-1;const result=typeof a==='number'&&typeof b==='number'?a-b:String(a).localeCompare(String(b));return sortDescending?-result:result}),columnCount=columns.length+2,header=(key,label)=>`<th><button type="button" data-sort="${esc(key)}">${esc(label)}${sortKey===key?` <span aria-hidden="true">${sortDescending?'▼':'▲'}</span>`:''}</button></th>`,folderRows=shownFolders.map(folder=>`<tr class="folder-row" data-folder="${folders.indexOf(folder)}"><td colspan="${columnCount}"><span class="tag">folder</span> <strong>${esc(folder.name)}</strong></td></tr>`).join(''),itemRows=shown.map(item=>`<tr class="item-row" data-item="${esc(item.id)}"><td><div class="item-name"><strong>${esc(item.title)}</strong>${item.subtitle?`<small>${esc(item.subtitle)}</small>`:''}</div></td>${columns.map(column=>`<td>${discoveryValue(item.summary_fields?.[column.key],column)}</td>`).join('')}<td><div class="item-tags">${(item.tags||[]).map(tag=>`<span class="tag">${esc(tag)}</span>`).join('')}</div></td></tr>`).join(''),folderActions=shownFolders.map(()=>'<div class="item-action-row folder-spacer"></div>').join(''),itemActions=shown.map(item=>`<div class="item-action-row">${batchControlsHtml(item.batch,`/workspaces/${encodeURIComponent(id)}/items/${encodeURIComponent(item.id)}/batch`)}</div>`).join('');
      document.querySelector('#items').innerHTML=folderRows||itemRows?`<div class="item-browser-layout" data-item-browser-layout><div class="item-browser"><table><thead><tr>${header('title','Name')}${columns.map(column=>header(column.key,column.label)).join('')}<th class="tags-column">Tags</th></tr></thead><tbody>${folderRows}${itemRows}</tbody></table></div><aside class="item-action-rail" aria-label="Item actions"><div class="item-action-rail-head"></div>${folderActions}${itemActions}</aside></div>`:'<div class="empty">No matching items.</div>';
      document.querySelectorAll('[data-sort]').forEach(button=>button.onclick=()=>{const key=button.dataset.sort;if(sortKey===key)sortDescending=!sortDescending;else{sortKey=key;sortDescending=false}draw()});
      document.querySelectorAll('[data-folder]').forEach(element=>element.onclick=()=>items(id,name,true,folders[Number(element.dataset.folder)].path));
      document.querySelectorAll('[data-item]').forEach(element=>element.onclick=()=>openItem(id,name,element.dataset.item));
      bindBatchMenus();bindItemActionRail()
    };
    draw();bindBatchMenus();document.querySelector('#home')?.addEventListener('click',()=>catalog());document.querySelector('#workspace-root').onclick=()=>items(id,name,true,[]);document.querySelectorAll('[data-directory-level]').forEach(element=>element.onclick=()=>items(id,name,true,directory.slice(0,Number(element.dataset.directoryLevel))));document.querySelector('#search').oninput=draw
  }catch(e){fail(e)}
}
async function openItem(wid,wname,iid,navigate=true,controlValues={},preservePlayback=false){
  stopPlayback();app.innerHTML='<div class="empty">Opening item…</div>';app.className='item-page';activeThemeRefresh=null;workspaceAdd.hidden=true;headerDetails.hidden=true;headerDownload.hidden=true;headerDownload.open=false;headerAnnotate.hidden=true;headerAnnotate.open=false;if(!preservePlayback){playbackPosition=0;playbackPaused=false;playbackFollowLive=false;windowStart=0;windowEnd=null;segmentId=null;Object.keys(viewSelections).forEach(key=>delete viewSelections[key])}if(navigate)history.pushState(null,'',`/workspace/${encodeURIComponent(wid)}/item/${encodeURIComponent(iid)}`);
  try{const request=async values=>api(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}?${new URLSearchParams(values)}`),windowValues=()=>windowEnd==null?{}:{__window_start_seconds:windowStart,__window_end_seconds:windowEnd},segmentValues=()=>segmentId==null?{}:{__segment_id:segmentId};let data=await request({...controlValues,...windowValues(),...segmentValues(),__theme:resolvedTheme(),__playback_time_seconds:playbackPosition});let p=data.page,requestGeneration=0;const isPlayback=['seek','live'].includes(p.playback.mode),isWindowed=p.playback.mode==='windowed',isSegmented=p.playback.mode==='segmented';annotations=p.annotation?.entries||[];
    const playbackConfig=p.playback;annotationTimelineColorControl=p.annotation?.timeline_color_control||null;if(isWindowed&&windowEnd==null){windowStart=Number(playbackConfig.window_start_seconds)||0;windowEnd=Number(playbackConfig.window_end_seconds)||playbackConfig.duration_seconds}if(isSegmented)segmentId=playbackConfig.selected_segment_id;headerDetails.hidden=false;headerDownload.hidden=!p.export?.enabled;headerAnnotate.hidden=!p.annotation?.enabled;configureCapabilityMenus(p);const hasWindowOverview=(playbackConfig.overview_values||[]).length>0||(playbackConfig.overview_heatmap||[]).length>0,playbackToolbar=isPlayback?`<div class="data-toolbar"><div class="playback-bar" id="playback-bar"><button class="primary" id="toggle">${playbackPaused?'▶ Play':'❚❚ Pause'}</button><div class="playback-track"><input id="position" aria-label="Playback position" type="range" min="0" value="${playbackPosition}"><div class="annotation-markers" data-annotation-markers aria-label="Annotations"></div></div><input id="current-time" aria-label="Current playback time in seconds" type="number" min="0" step="any" value="${playbackPosition}"><span id="counter"></span>${playbackConfig.mode==='live'?'<button class="live-toggle" id="jump-live">Live</button>':''}</div></div>`:isWindowed?`<div class="data-toolbar"><div class="windowed-bar" id="windowed-bar"><input class="windowed-time" id="windowed-start" aria-label="Window start time in seconds" type="number" min="0" max="${playbackConfig.duration_seconds}" step="any"><div class="windowed-track-stack ${hasWindowOverview?'has-overview':''}"><div class="windowed-track" id="windowed-track">${hasWindowOverview?`<span class="windowed-label" title="${esc(playbackConfig.overview_label||'Signal overview')}">${esc(playbackConfig.overview_label||'Signal overview')}</span>`:''}<canvas class="windowed-overview" aria-hidden="true"></canvas><div class="annotation-markers" data-annotation-markers aria-label="Annotations"></div><button class="windowed-selection" id="windowed-selection" type="button" aria-label="Move selected window"></button><button class="windowed-handle" id="windowed-left" type="button" role="slider" aria-label="Window start" aria-valuemin="0" aria-valuemax="${playbackConfig.duration_seconds}"></button><button class="windowed-handle" id="windowed-right" type="button" role="slider" aria-label="Window end" aria-valuemin="0" aria-valuemax="${playbackConfig.duration_seconds}"></button><button class="windowed-full-extent" id="windowed-full-extent" type="button" aria-label="Select full recording" title="Select full recording">↔</button></div></div><input class="windowed-time" id="windowed-end" aria-label="Window stop time in seconds" type="number" min="0" max="${playbackConfig.duration_seconds}" step="any"><span class="windowed-total" id="windowed-total"></span><label class="windowed-width-label"><input class="windowed-width" id="windowed-width" aria-label="Window buffer width in seconds" type="number" min="${playbackConfig.minimum_window_seconds}" max="${playbackConfig.duration_seconds}" step="any"> <span id="windowed-unit">s buffer</span></label></div></div>`:isSegmented?`<div class="data-toolbar"><div class="segmented-bar" id="segmented-bar"><div class="segmented-track" id="segmented-track" aria-label="Available result segments"></div><span class="segment-count" id="segment-count"></span><span class="segment-time" id="segment-time"></span><div class="segment-actions"><button id="segment-previous" type="button">Previous</button><button id="segment-next" type="button">Next</button></div><div class="segment-playback"><button id="segment-toggle" type="button" aria-label="Play segments" title="Play segments">▶</button><label class="segment-number"><input id="segment-rate" type="number" min="0.1" max="30" step="0.1" value="1" aria-label="Segment refresh rate"><span>Hz</span></label><label class="segment-number segment-step"><input id="segment-step" type="number" min="1" step="1" value="1" aria-label="Segments per refresh"><span>step</span></label></div></div></div>`:'';app.innerHTML=`${playbackToolbar}${sidebarHtml(wname,p)}<section class="data-stage"><div id="active-view" class="view"></div></section>`;
    let rasterRefreshTimer=null,lastRasterViewportSignature='{}';const plotViewportPayload=()=>Object.fromEntries([...document.querySelectorAll('[data-plot-view]')].map(plot=>[plot.dataset.plotView,Object.fromEntries(Object.entries(currentPlotViewport(plot)).map(([name,range])=>[name,{range,base:plot._sigvueResetRanges?.[`${name}.range`]}]))]).filter(([,viewport])=>Object.keys(viewport).length));const scheduleRasterRefresh=()=>{clearTimeout(rasterRefreshTimer);rasterRefreshTimer=setTimeout(()=>{const signature=JSON.stringify(plotViewportPayload());if(signature===lastRasterViewportSignature)return;lastRasterViewportSignature=signature;void refresh(true,false,true)},180)};
    let activeViewChanged=null;const browserStarted=performance.now(),segmentActions=document.querySelector('.segment-actions'),segmentedBar=document.querySelector('.segmented-bar');if(segmentActions&&segmentedBar)segmentedBar.prepend(segmentActions);document.querySelector('#active-view').innerHTML=renderLayout(p.layout,p.rendered_views,p.controls,p.control_values);bindLayoutTabs(()=>activeViewChanged?.());bindViewSwitchers(()=>activeViewChanged?.());bindColormapPickers();bindSidebar();observeDataStage();void Promise.all([initializePlotlyViews(p.rendered_views,scheduleRasterRefresh),preloadMatplotlibViews(p.rendered_views)]).then(()=>setClientRuntime('browser-runtime',performance.now()-browserStarted));requestAnimationFrame(resizePlots);
    const selected=()=>({...Object.fromEntries([...document.querySelectorAll('[data-control]')].map(c=>[c.dataset.control,c.type==='checkbox'?String(c.checked):c.value])),...Object.fromEntries(Object.entries(viewSelections).map(([key,index])=>[`__view_selection_${key}`,index])),...windowValues(),...segmentValues(),__theme:resolvedTheme(),__playback_follow_live:playbackFollowLive,__plot_viewports:JSON.stringify(plotViewportPayload())});
    const annotationPosition=()=>isWindowed?windowStart:isSegmented?Number((playbackConfig.segments||[]).find(segment=>segment.identifier===segmentId)?.start_seconds||0):playbackPosition;
    const annotationDuration=()=>isWindowed?windowEnd-windowStart:isSegmented?Number((playbackConfig.segments||[]).find(segment=>segment.identifier===segmentId)?.duration_seconds||0):null;
    document.querySelector('#annotation-form').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget,button=form.querySelector('button'),values=Object.fromEntries([...form.querySelectorAll('[data-annotation-field]')].map(field=>[field.dataset.annotationField,field.value]));button.disabled=true;button.textContent='Saving…';try{const result=await apiPost(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}/annotations`,{control_values:{...selected(),__playback_time_seconds:playbackPosition},position_seconds:annotationPosition(),duration_seconds:annotationDuration(),values});annotations.push(result);renderAnnotationMarkers(playbackConfig);headerAnnotate.open=false;form.reset();try{await refresh(true)}catch(refreshError){alert(`Annotation saved, but the plots could not refresh: ${refreshError.message}`)}}catch(error){alert(`Annotation failed: ${error.message}`)}finally{button.disabled=false;button.textContent='Add annotation'}};
    document.querySelector('#download-form').onsubmit=async event=>{event.preventDefault();const button=event.currentTarget.querySelector('button'),scope=document.querySelector('#export-scope').value,format=document.querySelector('#export-format').value;button.disabled=true;button.textContent='Preparing…';try{const job=await apiPost(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}/exports`,{control_values:{...selected(),__playback_time_seconds:playbackPosition},scope,format});let status;do{await new Promise(resolve=>setTimeout(resolve,350));status=await api(job.status_url)}while(status.status==='pending'||status.status==='running');if(status.status==='error')throw new Error(status.detail);for(const file of status.files){const link=document.createElement('a');link.href=file.url;link.download=file.name;link.click()}headerDownload.open=false}catch(error){alert(`Export failed: ${error.message}`)}finally{button.disabled=false;button.textContent='Download'}};
    const refresh=async(includeStatic=false,commitRequestedTheme=false,rasterOnly=false)=>{const generation=++requestGeneration,result=await request({...selected(),__playback_time_seconds:playbackPosition,__include_static_views:includeStatic});if(generation!==requestGeneration)return false;const browserStarted=performance.now();data=result;Object.assign(playbackConfig,result.page.playback);p=result.page;p.playback=playbackConfig;annotationTimelineColorControl=p.annotation?.timeline_color_control||null;if(Array.isArray(p.annotation?.entries))annotations=p.annotation.entries;if(playbackFollowLive)playbackPosition=playbackConfig.duration_seconds;if(commitRequestedTheme)await preloadMatplotlibViews(p.rendered_views);const render=async()=>{if(commitRequestedTheme)applyTheme();if(!rasterOnly)updateStatistics(p.statistics,p.runtime_statistics);const mounted=mountRenderedViews(p.rendered_views),mountedNames=new Set(mounted.map(view=>view.name)),updates=p.rendered_views.filter(view=>!mountedNames.has(view.name));await initializePlotlyViews(mounted,scheduleRasterRefresh);await updatePlotlyViews(updates);if(!rasterOnly){await updateMatplotlibViews(p.rendered_views);updateGenericViews(p.rendered_views)}setClientRuntime('browser-runtime',performance.now()-browserStarted)};if(commitRequestedTheme)await commitTheme(render);else await render();return true};activeViewChanged=()=>p.lazy_views?refresh(true):Promise.resolve(true);activeThemeRefresh=async()=>{if(isPlayback)clearInterval(playbackTimer);const applied=await refresh(true,true);if(isPlayback&&applied)startFrameworkPlayback(p.playback,refresh);else if(isWindowed&&applied)startFrameworkWindowed(p.playback,refresh,p.controls);else if(isSegmented&&applied)startFrameworkSegmented(p.playback,refresh)};
    const settingsChanged=async()=>{redrawWindowOverview?.();if(isPlayback)clearInterval(playbackTimer);const applied=await refresh(true);if(isPlayback&&applied)startFrameworkPlayback(p.playback,refresh);else if(isWindowed&&applied)startFrameworkWindowed(p.playback,refresh,p.controls);else if(isSegmented&&applied)startFrameworkSegmented(p.playback,refresh)};
    bindLimitsPickers(settingsChanged);if(isPlayback)startFrameworkPlayback(p.playback,refresh);else if(isWindowed)startFrameworkWindowed(p.playback,refresh,p.controls);else if(isSegmented)startFrameworkSegmented(p.playback,refresh);else if(p.refresh.enabled)startFrameworkRefresh(p.refresh,refresh);document.querySelectorAll('[data-control]').forEach(x=>{if(x.closest('[data-limits-picker]'))return;x.onchange=settingsChanged;if(x.type==='color')x.oninput=()=>{const swatch=x.closest('[data-style-picker]')?.querySelector('[data-style-swatch]');if(swatch)swatch.style.background=x.value;updateAnnotationMarkerColor()}});document.querySelector('#home')?.addEventListener('click',()=>catalog());document.querySelector('#back').onclick=()=>items(wid,wname,true,data.item.navigation_path||[])
  }catch(e){fail(e)}}
async function boot(reload=false){const parts=location.pathname.split('/').filter(Boolean).map(decodeURIComponent),workspaceUrl=reload?'/workspaces?reload=1':'/workspaces';if(parts[0]==='results'&&['job','saved'].includes(parts[1])&&parts[2]&&!parts[3]){const path=new URLSearchParams(location.search).get('path')||'',directory=path.split('/').filter(Boolean);return resultBrowser(parts[1],parts[2],null,false,directory)}if(parts[0]==='results'&&parts[1]&&parts[2]&&parts[3])return resultBrowser(parts[1],parts[2],parts[3],false,parts.slice(4));if(parts[0]!=='workspace'){if(reload)await api(workspaceUrl);return catalog(false)}try{const {workspaces}=await api(workspaceUrl);singleWorkspaceMode=workspaces.length===1;const workspace=workspaces.find(w=>w.id===parts[1]);if(!workspace)return catalog(false);if(parts[2]==='item'&&parts[3])return openItem(workspace.id,workspace.name,parts[3],false);if(parts[2]==='browse')return items(workspace.id,workspace.name,false,parts.slice(3));return items(workspace.id,workspace.name,false)}catch(e){fail(e)}}
appHome.onclick=()=>catalog();
window.onpopstate=event=>{routeIndex=Number(event.state?.sigvueIndex??0);syncHeaderNavigation();boot()};syncHeaderNavigation();syncBatchNotifications();boot();
</script></body></html>"""


@dataclass(frozen=True)
class WorkspaceModuleRegistration:
    module_name: str
    attribute: str
    watch_path: Path | None = None
    configuration: dict[str, Any] = field(default_factory=dict)
    metadata_overrides: dict[str, Any] = field(default_factory=dict)
    reference: str | None = None
    flatten_discovery: bool | None = None


class _ConfiguredWorkspace:
    """Delegate behavior while applying browser-owned instance settings."""

    def __init__(
        self,
        workspace: Any,
        overrides: dict[str, Any],
        flatten_discovery: bool | None = None,
    ) -> None:
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
        self.flatten_discovery = (
            workspace.flatten_discovery
            if flatten_discovery is None
            else flatten_discovery
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._workspace, name)


@dataclass
class ExportJob:
    directory: Path
    future: Future[dict[str, object]]


@dataclass
class BatchProgress:
    _snapshot: dict[str, object] | None = None
    _lock: RLock = field(default_factory=RLock, repr=False)

    def update(self, snapshot: dict[str, object]) -> None:
        with self._lock:
            self._snapshot = {
                **snapshot,
                "items": [
                    dict(item)
                    for item in snapshot.get("items", [])
                    if isinstance(item, dict)
                ],
            }

    def snapshot(self) -> dict[str, object] | None:
        with self._lock:
            if self._snapshot is None:
                return None
            return {
                **self._snapshot,
                "items": [
                    dict(item)
                    for item in self._snapshot.get("items", [])
                    if isinstance(item, dict)
                ],
            }


@dataclass
class BatchJob:
    workspace_id: str
    workspace_name: str
    item_id: str | None
    item_title: str | None
    action: str
    action_label: str
    directory: Path
    future: Future[dict[str, object]]
    started_at: float
    temporary: bool = True
    progress: BatchProgress = field(default_factory=BatchProgress)
    cancel_event: Event = field(default_factory=Event, repr=False)
    declared_files: tuple[str, ...] = ()
    initial_outputs: dict[str, tuple[int, int]] = field(default_factory=dict)
    started_ns: int = 0


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
    _profile_workspace_modules: tuple[WorkspaceModuleRegistration, ...] = field(
        default=(),
        init=False,
        repr=False,
    )
    _session_workspace_modules: tuple[WorkspaceModuleRegistration, ...] = field(
        default=(),
        init=False,
        repr=False,
    )
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
    _batch_declared_entries: dict[str, Path] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _batch_declared_collections: dict[
        str,
        tuple[Path, tuple[str, ...]],
    ] = field(default_factory=dict, init=False, repr=False)
    _batch_executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=4, thread_name_prefix="workspace-batch"),
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = WorkspaceRegistry()
        self._fixed_workspaces = self.registry.list()
        self._profile_workspace_modules = self.workspace_modules
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
        configuration: dict[str, Any] | None = None,
        metadata_overrides: dict[str, Any] | None = None,
        reference: str | None = None,
        flatten_discovery: bool | None = None,
    ) -> None:
        """Add a configured workspace factory for this application session."""
        registration = WorkspaceModuleRegistration(
            module_name,
            attribute,
            Path(watch_path).resolve() if watch_path is not None else None,
            dict(configuration or {}),
            dict(metadata_overrides or {}),
            reference,
            flatten_discovery,
        )
        self.add_workspace_spec(
            WorkspaceLaunchSpec(
                registration.module_name,
                registration.attribute,
                registration.configuration,
                registration.watch_path,
                registration.metadata_overrides,
                registration.reference,
                registration.flatten_discovery,
            )
        )

    def add_workspace_spec(self, spec: WorkspaceLaunchSpec) -> dict[str, Any]:
        """Instantiate and retain one configured workspace for this session."""
        registration = _profile_registration(spec)
        with self._reload_lock:
            previous_identifiers = {
                workspace.metadata.identifier for workspace in self.registry.list()
            }
            previous_modules = self.workspace_modules
            previous_session = self._session_workspace_modules
            previous_registry = self.registry
            previous_snapshot = self._workspace_snapshot
            try:
                self._session_workspace_modules = (
                    *self._session_workspace_modules,
                    registration,
                )
                self.workspace_modules = (
                    *self._profile_workspace_modules,
                    *self._session_workspace_modules,
                )
                self.reload_workspace_modules(force=True)
                added = [
                    workspace
                    for workspace in self.list_workspaces()
                    if workspace["id"] not in previous_identifiers
                ]
                if len(added) != 1:
                    raise RuntimeError(
                        "Workspace factory did not add exactly one workspace"
                    )
            except Exception:
                self.workspace_modules = previous_modules
                self._session_workspace_modules = previous_session
                self.registry = previous_registry
                self._workspace_snapshot = previous_snapshot
                raise
            return added[0]

    def configure_workspace(
        self,
        entry: dict[str, Any],
        *,
        base_directory: str | Path | None = None,
        persist_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Resolve and add a profile-shaped workspace entry to this session."""
        base = (
            Path(base_directory).expanduser().resolve()
            if base_directory is not None
            else Path.cwd()
        )
        spec = workspace_launch_spec(entry, base)
        with self._reload_lock:
            previous = (
                self.registry,
                self.workspace_modules,
                self._profile_workspace_modules,
                self._session_workspace_modules,
                self._workspace_snapshot,
                self.config_path,
                self.title,
                self.subtitle,
            )
            try:
                workspace = self.add_workspace_spec(spec)
                persisted_to = None
                if persist_path is not None:
                    persisted_to = append_workspace_to_profile(persist_path, spec)
                    registration = self._session_registration(workspace["id"])
                    if (
                        self.config_path is not None
                        and persisted_to == self.config_path
                    ):
                        self._session_workspace_modules = tuple(
                            candidate
                            for candidate in self._session_workspace_modules
                            if candidate is not registration
                        )
                        self.reload_browser_profile()
                    elif self.config_path is None:
                        self._session_workspace_modules = tuple(
                            candidate
                            for candidate in self._session_workspace_modules
                            if candidate is not registration
                        )
                        self.config_path = persisted_to
                        self.reload_browser_profile()
            except Exception:
                (
                    self.registry,
                    self.workspace_modules,
                    self._profile_workspace_modules,
                    self._session_workspace_modules,
                    self._workspace_snapshot,
                    self.config_path,
                    self.title,
                    self.subtitle,
                ) = previous
                raise
        return {
            "workspace": workspace,
            "persisted_to": str(persisted_to) if persisted_to else None,
        }

    def workspace_setup(
        self,
        repository: str | Path | None = None,
    ) -> dict[str, Any]:
        """Return local factory discovery and profile defaults for the wizard."""
        return {
            "factories": workspace_factory_catalog(repository),
            "workspaces": [
                {"id": workspace["id"], "name": workspace["name"]}
                for workspace in self.list_workspaces()
            ],
            "working_directory": str(Path.cwd()),
            "config_path": str(self.config_path) if self.config_path else None,
            "default_profile_path": str(
                self.config_path or (Path.cwd() / "browser.toml").resolve()
            ),
        }

    def _session_registration(
        self,
        identifier: str,
    ) -> WorkspaceModuleRegistration:
        for registration in reversed(self._session_workspace_modules):
            configured = (
                registration.metadata_overrides.get("identifier")
                or registration.configuration.get("id")
            )
            if configured == identifier:
                return registration
        raise ValueError(
            "Persisted session workspaces require an explicit instance identifier"
        )

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
                self._profile_workspace_modules,
                self._workspace_snapshot,
                self.title,
                self.subtitle,
            )
            try:
                self._profile_workspace_modules = registrations
                self.workspace_modules = (
                    *registrations,
                    *self._session_workspace_modules,
                )
                if self.workspace_modules:
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
                    self._profile_workspace_modules,
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
                if (
                    registration.metadata_overrides
                    or registration.flatten_discovery is not None
                ):
                    workspace = _ConfiguredWorkspace(
                        workspace,
                        registration.metadata_overrides,
                        registration.flatten_discovery,
                    )
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
                job_id = self._batch_latest.get(
                    (workspace_id, item_id, choice.value)
                )
                workspace_job_id = (
                    self._batch_latest.get(
                        (workspace_id, None, choice.value)
                    )
                    if item_id is not None
                    else None
                )
                job = self._batch_jobs.get(job_id) if job_id else None
                workspace_job = (
                    self._batch_jobs.get(workspace_job_id)
                    if workspace_job_id
                    else None
                )
                if (
                    workspace_job is not None
                    and (
                        job is None
                        or workspace_job.started_at >= job.started_at
                    )
                ):
                    status = self._workspace_batch_item_status(
                        workspace,
                        workspace_job_id,
                        item_id,
                        choice.value,
                    )
                elif job_id:
                    status = self.batch_status(job_id)
                else:
                    status = self._declared_batch_status(
                        workspace,
                        choice.value,
                        item_id,
                    )
                if status.get("status") == "cancelled":
                    status = {"status": "idle"}
                actions.append({**choice.__dict__, **status})
        return {"enabled": bool(actions), "actions": actions}

    def _workspace_batch_item_status(
        self,
        workspace: Any,
        job_id: str,
        item_id: str,
        action: str,
    ) -> dict[str, object]:
        """Project the latest group run onto one matching item action."""
        overall = self.batch_status(job_id)
        item = next(
            (
                candidate
                for candidate in overall.get("progress", {}).get(
                    "items",
                    (),
                )
                if str(candidate.get("id")) == item_id
            ),
            None,
        )
        if item is None:
            if overall["status"] == "ready":
                return self._declared_batch_status(
                    workspace,
                    action,
                    item_id,
                )
            return {"status": "idle"}

        item_status = str(item.get("status", "pending"))
        if item_status == "ready":
            durable = self._declared_batch_status(
                workspace,
                action,
                item_id,
            )
            return (
                durable
                if durable.get("status") == "ready"
                else {"status": "ready"}
            )
        if item_status == "running":
            return {"status": "running"}
        if item_status == "error":
            return {
                key: value
                for key, value in item.items()
                if key in {"status", "detail", "log"}
            }
        return {"status": "idle"}

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
            kind = "directory" if path.is_dir() else "file"
            encoded_name = quote(name, safe="")
            if job_id:
                url = f"/batches/{job_id}/{encoded_name}"
                browse_url = f"/results/job/{job_id}/{encoded_name}"
            else:
                token = uuid5(NAMESPACE_URL, str(path)).hex
                with self._batch_lock:
                    self._batch_declared_files[(token, name)] = path
                    self._batch_declared_entries[token] = path
                url = f"/batch-files/{token}/{encoded_name}"
                browse_url = f"/results/saved/{token}/{encoded_name}"
            files.append({
                "name": name,
                "path": str(path),
                "url": url,
                "kind": kind,
                "browse_url": browse_url if kind == "directory" else None,
                "open_url": (
                    url
                    if kind == "file"
                    and path.suffix.lower()
                    in _IMAGE_SUFFIXES | {".html", ".htm", ".pdf"}
                    else None
                ),
                "download_url": f"{url}?download=1" if kind == "file" else None,
            })
        return files

    def _declared_batch_collection_url(
        self,
        directory: Path,
        names: tuple[str, ...],
    ) -> str:
        token = uuid5(
            NAMESPACE_URL,
            "\0".join(("sigvue-batch-collection", str(directory), *names)),
        ).hex
        with self._batch_lock:
            self._batch_declared_collections[token] = (directory, names)
        return f"/results/saved/{token}"

    @staticmethod
    def _batch_path_signature(path: Path) -> tuple[int, int] | None:
        try:
            status = path.stat()
        except OSError:
            return None
        return status.st_mtime_ns, status.st_size

    @staticmethod
    def _transient_batch_name(name: str) -> bool:
        lowered = name.casefold()
        return (
            name.startswith(".")
            or lowered.endswith((".part", ".partial", ".tmp", ".download"))
        )

    @staticmethod
    def _finished_batch_result(job: BatchJob) -> dict[str, object] | None:
        if not job.future.done() or job.future.cancelled():
            return None
        try:
            return job.future.result()
        except BaseException:
            return None

    def _visible_batch_output_names(self, job: BatchJob) -> tuple[str, ...]:
        result = self._finished_batch_result(job)
        if result is not None:
            return tuple(result["files"])
        candidates = job.declared_files
        if not candidates:
            try:
                candidates = tuple(
                    entry.name
                    for entry in job.directory.iterdir()
                    if not self._transient_batch_name(entry.name)
                )
            except OSError:
                return ()
        visible = []
        for name in candidates:
            if self._transient_batch_name(name):
                continue
            path = job.directory / name
            signature = self._batch_path_signature(path)
            if signature is None or signature == job.initial_outputs.get(name):
                continue
            visible.append(name)
        return tuple(visible)

    def _declared_batch_status(self, workspace: Any, action: str, item_id: str | None) -> dict[str, object]:
        destination = self._batch_destination(workspace, action, item_id)
        if destination.directory is None or not destination.files:
            return {"status": "idle"}
        directory = destination.directory.expanduser().resolve()
        if all((directory / name).exists() for name in destination.files):
            return {
                "status": "ready",
                "summary": destination.summary,
                "files": self._batch_files(None, directory, destination.files),
                "result_browser_url": self._declared_batch_collection_url(
                    directory,
                    destination.files,
                ),
            }
        return {"status": "idle"}

    def start_batch(self, workspace_id: str, action: str, item_id: str | None = None) -> str:
        """Dispatch one workspace-defined item or workspace batch job."""
        workspace = self.registry.get(workspace_id)
        capability = getattr(workspace, "batch", None)
        choices = capability.item_actions if capability is not None and item_id is not None else (
            capability.workspace_actions if capability is not None else ()
        )
        choice = next((choice for choice in choices if choice.value == action), None)
        if choice is None:
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
        started_at = time.time()
        started_ns = time.time_ns()
        initial_outputs = {
            name: signature
            for name in destination.files
            if (
                signature
                := self._batch_path_signature(directory / name)
            )
            is not None
        }
        progress = BatchProgress()
        cancel_event = Event()

        def build() -> dict[str, object]:
            result = (
                workspace.run_item_batch(
                    item_id,
                    action,
                    directory,
                    _cancelled_callback=cancel_event.is_set,
                )
                if item_id is not None
                else workspace.run_workspace_batch(
                    action,
                    directory,
                    _progress_callback=progress.update,
                    _cancelled_callback=cancel_event.is_set,
                )
            )
            if cancel_event.is_set():
                raise CancelledError("Batch cancelled")
            if not isinstance(result, BatchResult):
                raise TypeError("Batch actions must return BatchResult")
            resolved_directory = directory.resolve()
            files = []
            for value in result.files:
                target = Path(value).resolve()
                if (
                    target.parent != resolved_directory
                    or not (target.is_file() or target.is_dir())
                ):
                    raise ValueError(
                        "Batch results must contain top-level files or "
                        "directories created in their destination directory"
                    )
                if any(character in target.name for character in "\r\n\0"):
                    raise ValueError("Batch result filenames cannot contain control characters")
                files.append(target.name)
            assets = []
            for value in result.assets:
                target = Path(value).resolve()
                try:
                    relative = target.relative_to(resolved_directory)
                except ValueError as exc:
                    raise ValueError(
                        "Batch assets must be created inside their destination directory"
                    ) from exc
                if not target.is_file():
                    raise ValueError("Batch assets must be files")
                relative_name = relative.as_posix()
                if any(character in relative_name for character in "\r\n\0"):
                    raise ValueError("Batch asset paths cannot contain control characters")
                assets.append(relative_name)
            missing_declared = [name for name in destination.files if name not in files]
            progress_snapshot = progress.snapshot() or {}
            if missing_declared and not progress_snapshot.get("failed"):
                raise ValueError(f"Batch result omitted declared files: {', '.join(missing_declared)}")
            return {
                "files": files,
                "assets": assets,
                "summary": result.summary,
            }

        future = self._batch_executor.submit(build)
        job = BatchJob(
            workspace_id=workspace_id,
            workspace_name=workspace.metadata.display_name,
            item_id=item_id,
            item_title=item_id,
            action=action,
            action_label=choice.label,
            directory=directory,
            future=future,
            started_at=started_at,
            temporary=temporary,
            progress=progress,
            cancel_event=cancel_event,
            declared_files=destination.files,
            initial_outputs=initial_outputs,
            started_ns=started_ns,
        )
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
        base = {
            "id": job_id,
            "workspace_id": job.workspace_id,
            "workspace_name": job.workspace_name,
            "item_id": job.item_id,
            "item_title": job.item_title,
            "action": job.action,
            "action_label": job.action_label,
            "started_at": job.started_at,
            "status_url": f"/batches/{job_id}",
            "result_browser_url": f"/results/job/{job_id}",
            "output_directory": str(job.directory),
        }
        progress = job.progress.snapshot()
        if progress is not None:
            base["progress"] = progress
        visible_files = self._visible_batch_output_names(job)
        if visible_files:
            base["files"] = self._batch_files(
                job_id,
                job.directory,
                visible_files,
            )
        if not job.future.done():
            files = self._batch_files(
                job_id,
                job.directory,
                self._visible_batch_output_names(job),
            )
            if job.cancel_event.is_set():
                return {
                    **base,
                    "status": "cancelling",
                    "files": files,
                }
            return {
                **base,
                "status": "running" if job.future.running() else "pending",
                "files": files,
            }
        try:
            result = job.future.result()
        except CancelledError:
            return {
                **base,
                "status": "cancelled",
                "summary": "Batch cancelled",
            }
        except Exception as exc:
            return {
                **base,
                "status": "error",
                "detail": str(exc) or type(exc).__name__,
                "log": "".join(format_exception(exc)),
            }
        missing = [
            name
            for name in (*result["files"], *result.get("assets", ()))
            if not (job.directory / name).exists()
        ]
        if missing:
            return {
                **base,
                "status": "error",
                "detail": f"Batch output is missing: {', '.join(missing)}",
            }
        summary = result["summary"]
        if progress is not None and progress.get("failed"):
            failed = int(progress["failed"])
            total = int(progress.get("total", failed))
            summary = f"{summary} · {failed} of {total} items failed"
            if total and failed == total:
                return {
                    **base,
                    "status": "error",
                    "detail": summary,
                    "files": self._batch_files(
                        job_id,
                        job.directory,
                        result["files"],
                    ),
                }
        return {
            **base,
            "status": "ready",
            "summary": summary,
            "files": self._batch_files(job_id, job.directory, result["files"]),
        }

    def cancel_batch(self, job_id: str) -> dict[str, object]:
        """Request cancellation of a queued or running batch job."""
        with self._batch_lock:
            job = self._batch_jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if not job.future.done():
            job.cancel_event.set()
            job.future.cancel()
        return self.batch_status(job_id)

    def batch_statuses(self) -> dict[str, list[dict[str, object]]]:
        """Return recent jobs so browser-level notifications survive navigation."""
        with self._batch_lock:
            job_ids = tuple(reversed(self._batch_jobs))
        jobs = []
        for job_id in job_ids:
            try:
                jobs.append(self.batch_status(job_id))
            except KeyError:
                continue
        return {"jobs": jobs}

    def batch_file(self, job_id: str, filename: str) -> Path:
        with self._batch_lock:
            job = self._batch_jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        target = (job.directory / filename).resolve()
        try:
            relative = target.relative_to(job.directory.resolve())
        except ValueError as exc:
            raise KeyError(filename) from exc
        if (
            not relative.parts
            or any(self._transient_batch_name(part) for part in relative.parts)
        ):
            raise KeyError(filename)
        result = self._finished_batch_result(job)
        output_names = self._visible_batch_output_names(job)
        allowed = set(output_names)
        if result is not None:
            allowed.update(result.get("assets", ()))
        if filename not in allowed:
            inside_result_directory = False
            for name in output_names:
                root = (job.directory / name).resolve()
                if not root.is_dir():
                    continue
                try:
                    target.relative_to(root)
                except ValueError:
                    continue
                inside_result_directory = True
                break
            if not inside_result_directory:
                for name in output_names:
                    entry = job.directory / name
                    if entry.suffix.lower() not in {".html", ".htm"}:
                        continue
                    sidecar = entry.with_name(
                        f"{entry.stem}.assets"
                    ).resolve()
                    try:
                        target.relative_to(sidecar)
                    except ValueError:
                        continue
                    inside_result_directory = True
                    break
            if not inside_result_directory:
                raise KeyError(filename)
        if not target.exists():
            raise KeyError(filename)
        return target

    @staticmethod
    def _directory_listing(
        root: Path,
        relative_path: str,
        url_prefix: str,
        *,
        minimum_mtime_ns: int | None = None,
    ) -> dict[str, object]:
        root = root.resolve()
        requested = Path(relative_path)
        if requested.is_absolute() or ".." in requested.parts:
            raise KeyError(relative_path)
        current = (root / requested).resolve()
        try:
            current.relative_to(root)
        except ValueError as exc:
            raise KeyError(relative_path) from exc
        if not current.is_dir():
            raise KeyError(relative_path)
        entries = []
        for entry in sorted(
            current.iterdir(),
            key=lambda value: (not value.is_dir(), value.name.casefold()),
        ):
            if SigvueApp._transient_batch_name(entry.name):
                continue
            try:
                resolved = entry.resolve()
                relative = resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if resolved.is_dir():
                kind = "directory"
                size = None
                version = None
            elif resolved.is_file():
                try:
                    status = resolved.stat()
                except OSError:
                    continue
                if (
                    minimum_mtime_ns is not None
                    and status.st_mtime_ns < minimum_mtime_ns
                ):
                    continue
                kind = (
                    "image"
                    if resolved.suffix.lower() in _IMAGE_SUFFIXES
                    else "file"
                )
                size = status.st_size
                version = status.st_mtime_ns
            else:
                continue
            encoded_path = "/".join(
                quote(part, safe="") for part in relative.parts
            )
            url = f"{url_prefix}/{encoded_path}"
            entries.append(
                {
                    "name": entry.name,
                    "relative_path": relative.as_posix(),
                    "path": str(entry.absolute()),
                    "kind": kind,
                    "size": size,
                    "version": version,
                    "url": url if kind != "directory" else None,
                    "open_url": (
                        url
                        if kind == "file"
                        and resolved.suffix.lower()
                        in _INLINE_SUFFIXES
                        else None
                    ),
                    "download_url": (
                        f"{url}?download=1"
                        if kind != "directory"
                        else None
                    ),
                }
            )
        return {
            "name": root.name,
            "path": str(current),
            "relative_path": requested.as_posix()
            if requested != Path(".")
            else "",
            "entries": entries,
        }

    def batch_directory(
        self,
        job_id: str,
        root_name: str,
        relative_path: str = "",
    ) -> dict[str, object]:
        """List an available directory result while its batch may still run."""
        with self._batch_lock:
            job = self._batch_jobs.get(job_id)
        if (
            job is None
            or root_name not in self._visible_batch_output_names(job)
        ):
            raise KeyError(job_id)
        root = (job.directory / root_name).resolve()
        if not root.is_dir():
            raise KeyError(root_name)
        prefix = f"/batches/{job_id}/{quote(root_name, safe='')}"
        status = self.batch_status(job_id)
        return {
            **self._directory_listing(
                root,
                relative_path,
                prefix,
                minimum_mtime_ns=(
                    None
                    if status["status"] == "ready"
                    else job.started_ns
                ),
            ),
            "status": status["status"],
            "complete": status["status"] not in {
                "pending",
                "running",
                "cancelling",
            },
        }

    def batch_outputs(
        self,
        job_id: str,
        relative_path: str = "",
    ) -> dict[str, object]:
        """List primary outputs as each one becomes available."""
        with self._batch_lock:
            job = self._batch_jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        status = self.batch_status(job_id)
        output_names = self._visible_batch_output_names(job)
        complete = status["status"] not in {
            "pending",
            "running",
            "cancelling",
        }
        requested = Path(relative_path)
        if requested.is_absolute() or ".." in requested.parts:
            raise KeyError(relative_path)
        parts = () if requested == Path(".") else requested.parts
        if parts:
            root_name, *children = parts
            if root_name not in output_names:
                raise KeyError(root_name)
            root = (job.directory / root_name).resolve()
            if not root.is_dir():
                raise KeyError(root_name)
            prefix = f"/batches/{job_id}/{quote(root_name, safe='')}"
            return {
                **self._directory_listing(
                    root,
                    Path(*children).as_posix() if children else "",
                    prefix,
                    minimum_mtime_ns=(
                        None if complete else job.started_ns
                    ),
                ),
                "status": status["status"],
                "complete": complete,
            }

        entries = []
        for name in output_names:
            target = (job.directory / name).resolve()
            if target.is_dir():
                kind = "directory"
                size = None
                version = None
            elif target.is_file():
                try:
                    target_status = target.stat()
                except OSError:
                    continue
                kind = (
                    "image"
                    if target.suffix.lower() in _IMAGE_SUFFIXES
                    else "file"
                )
                size = target_status.st_size
                version = target_status.st_mtime_ns
            else:
                continue
            url = f"/batches/{job_id}/{quote(name, safe='')}"
            entries.append(
                {
                    "name": name,
                    "relative_path": name,
                    "path": str(target),
                    "kind": kind,
                    "size": size,
                    "version": version,
                    "url": url if kind != "directory" else None,
                    "open_url": (
                        url
                        if kind == "file"
                        and target.suffix.lower() in _INLINE_SUFFIXES
                        else None
                    ),
                    "download_url": (
                        f"{url}?download=1"
                        if kind != "directory"
                        else None
                    ),
                }
            )
        return {
            "name": "Batch results",
            "path": str(job.directory),
            "relative_path": "",
            "entries": entries,
            "status": status["status"],
            "complete": complete,
        }

    def batch_assets(self, job_id: str) -> tuple[str, ...]:
        """Return relative support-file paths for a completed batch result."""
        with self._batch_lock:
            job = self._batch_jobs.get(job_id)
        if (
            job is None
            or not job.future.done()
            or job.future.exception() is not None
        ):
            raise KeyError(job_id)
        return tuple(job.future.result().get("assets", ()))

    def declared_batch_file(self, token: str, filename: str) -> Path:
        with self._batch_lock:
            target = self._batch_declared_files.get((token, filename))
            entry = self._batch_declared_entries.get(token)
            collection = self._batch_declared_collections.get(token)
        if collection is not None:
            directory, names = collection
            target = (directory / filename).resolve()
            try:
                relative = target.relative_to(directory.resolve())
            except ValueError as exc:
                raise KeyError(filename) from exc
            if (
                not relative.parts
                or any(
                    self._transient_batch_name(part)
                    for part in relative.parts
                )
                or not target.is_file()
            ):
                raise KeyError(filename)
            root_name = relative.parts[0]
            root = (directory / root_name).resolve()
            if root_name in names and len(relative.parts) == 1:
                return target
            if root_name in names and root.is_dir():
                return target
            html = next(
                (
                    (directory / name).resolve()
                    for name in names
                    if Path(name).suffix.lower() in {".html", ".htm"}
                    and f"{Path(name).stem}.assets" == root_name
                ),
                None,
            )
            if html is not None:
                target.relative_to(
                    html.with_name(f"{html.stem}.assets").resolve()
                )
                return target
            raise KeyError(filename)
        if target is not None and target.exists():
            return target
        if entry is not None and entry.is_dir():
            target = (entry.parent / filename).resolve()
            try:
                target.relative_to(entry.resolve())
            except ValueError as exc:
                raise KeyError(filename) from exc
            if not target.exists():
                raise KeyError(filename)
            return target
        if (
            entry is None
            or entry.suffix.lower() not in {".html", ".htm"}
        ):
            raise KeyError(filename)
        asset_root = entry.with_name(f"{entry.stem}.assets").resolve()
        target = (entry.parent / filename).resolve()
        try:
            target.relative_to(asset_root)
        except ValueError as exc:
            raise KeyError(filename) from exc
        if not target.is_file():
            raise KeyError(filename)
        return target

    def declared_batch_outputs(
        self,
        token: str,
        relative_path: str = "",
    ) -> dict[str, object]:
        """List a durable output collection rediscovered after relaunch."""
        with self._batch_lock:
            collection = self._batch_declared_collections.get(token)
        if collection is None:
            raise KeyError(token)
        directory, names = collection
        requested = Path(relative_path)
        if requested.is_absolute() or ".." in requested.parts:
            raise KeyError(relative_path)
        parts = () if requested == Path(".") else requested.parts
        if parts:
            root_name, *children = parts
            if root_name not in names:
                raise KeyError(root_name)
            root = (directory / root_name).resolve()
            if not root.is_dir():
                raise KeyError(root_name)
            prefix = f"/batch-files/{token}/{quote(root_name, safe='')}"
            return {
                **self._directory_listing(
                    root,
                    Path(*children).as_posix() if children else "",
                    prefix,
                ),
                "status": "ready",
                "complete": True,
            }

        entries = []
        for name in names:
            target = (directory / name).resolve()
            if target.is_dir():
                kind = "directory"
                size = None
                version = None
            elif target.is_file():
                status = target.stat()
                kind = (
                    "image"
                    if target.suffix.lower() in _IMAGE_SUFFIXES
                    else "file"
                )
                size = status.st_size
                version = status.st_mtime_ns
            else:
                continue
            url = f"/batch-files/{token}/{quote(name, safe='')}"
            entries.append(
                {
                    "name": name,
                    "relative_path": name,
                    "path": str(target),
                    "kind": kind,
                    "size": size,
                    "version": version,
                    "url": url if kind != "directory" else None,
                    "open_url": (
                        url
                        if kind == "file"
                        and target.suffix.lower() in _INLINE_SUFFIXES
                        else None
                    ),
                    "download_url": (
                        f"{url}?download=1"
                        if kind != "directory"
                        else None
                    ),
                }
            )
        return {
            "name": "Batch results",
            "path": str(directory),
            "relative_path": "",
            "entries": entries,
            "status": "ready",
            "complete": True,
        }

    def declared_batch_directory(
        self,
        token: str,
        root_name: str,
        relative_path: str = "",
    ) -> dict[str, object]:
        """List a durable directory result rediscovered after a relaunch."""
        with self._batch_lock:
            root = self._batch_declared_entries.get(token)
        if root is None or not root.is_dir() or root.name != root_name:
            raise KeyError(token)
        prefix = f"/batch-files/{token}/{quote(root_name, safe='')}"
        return self._directory_listing(root, relative_path, prefix)

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
        rendered_views = []
        callbacks_started = time.perf_counter()
        active_views = (
            set(selected_view_names(opened.page.layout, requested_values))
            if bool(getattr(workspace, "lazy_views", False))
            else {view.name for view in opened.page.views}
        )
        for view in opened.page.views:
            if view.name not in active_views:
                continue
            if view.update_policy == "static" and not include_static:
                continue
            value = view.callback(values)
            rasterized = bool(getattr(value, "_sigvue_viewport_heatmap", False))
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
                "lazy_views": bool(getattr(workspace, "lazy_views", False)),
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
        """Run a workspace export on the dedicated export executor."""
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
        spec.reference,
        spec.flatten_discovery,
    )


def create_app(
    title: str = "Sigvue",
    *,
    subtitle: str = "Explore scientific and analytical results",
    reload_workspaces: bool = True,
    config_path: str | Path | None = None,
    workspace_specs: tuple[WorkspaceLaunchSpec, ...] = (),
) -> SigvueApp:
    if config_path is not None:
        profile = load_browser_profile(config_path)
        app = SigvueApp(
            title=profile.title or title,
            subtitle=profile.subtitle or subtitle,
            reload_workspaces=reload_workspaces,
            workspace_modules=tuple(
                _profile_registration(spec) for spec in profile.workspaces
            ),
            config_path=Path(config_path).expanduser().resolve(),
        )
    else:
        app = SigvueApp(
            title=title,
            subtitle=subtitle,
            reload_workspaces=reload_workspaces,
        )
    for spec in workspace_specs:
        app.add_workspace_spec(spec)
    return app


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
            if (
                parsed.path == "/"
                or parsed.path.startswith("/workspace/")
                or parsed.path.startswith("/results/")
            ):
                if app.config_path is not None:
                    app.reload_browser_profile()
                body = _INDEX_HTML.replace("__BROWSER_TITLE__", html_escape(app.title))
                body = body.replace("__BROWSER_SUBTITLE__", html_escape(app.subtitle))
                self._write_html(body)
                return
            if parsed.path == "/health":
                self._write_json(200, {"status": "ok"})
                return
            if parsed.path == "/workspace-setup":
                try:
                    repository = parse_qs(parsed.query).get("repository", [None])[-1]
                    self._write_json(200, app.workspace_setup(repository))
                except ValueError as exc:
                    self._write_json(
                        400,
                        {"error": "bad_request", "detail": str(exc)},
                    )
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
            if parsed.path == "/batches":
                self._write_json(200, app.batch_statuses())
                return

            parts = [unquote(segment) for segment in parsed.path.split("/") if segment]
            try:
                if (
                    len(parts) == 3
                    and parts[0] == "batch-browser"
                ):
                    relative_path = parse_qs(parsed.query).get("path", [""])[-1]
                    if parts[1] == "job":
                        listing = app.batch_outputs(
                            parts[2],
                            relative_path,
                        )
                    elif parts[1] == "saved":
                        listing = app.declared_batch_outputs(
                            parts[2],
                            relative_path,
                        )
                    else:
                        raise KeyError(parts[1])
                    self._write_json(200, listing)
                    return
                if len(parts) == 4 and parts[0] == "batch-browser":
                    relative_path = parse_qs(parsed.query).get("path", [""])[-1]
                    if parts[1] == "job":
                        listing = app.batch_directory(
                            parts[2],
                            parts[3],
                            relative_path,
                        )
                    elif parts[1] == "saved":
                        listing = app.declared_batch_directory(
                            parts[2],
                            parts[3],
                            relative_path,
                        )
                    else:
                        raise KeyError(parts[1])
                    self._write_json(200, listing)
                    return
                if len(parts) == 2 and parts[0] == "exports":
                    self._write_json(200, app.export_status(parts[1]))
                    return
                if len(parts) == 2 and parts[0] == "batches":
                    self._write_json(200, app.batch_status(parts[1]))
                    return
                if len(parts) >= 3 and parts[0] == "batches":
                    filename = "/".join(parts[2:])
                    batch_path = app.batch_file(parts[1], filename)
                    if batch_path.exists() and not batch_path.is_file():
                        raise KeyError(filename)
                    self._write_export_file(
                        batch_path,
                        inline=(
                            "download" not in parse_qs(parsed.query)
                            and batch_path.suffix.lower() in _INLINE_SUFFIXES
                        ),
                    )
                    return
                if len(parts) >= 3 and parts[0] == "batch-files":
                    filename = "/".join(parts[2:])
                    batch_path = app.declared_batch_file(parts[1], filename)
                    if batch_path.exists() and not batch_path.is_file():
                        raise KeyError(filename)
                    self._write_export_file(
                        batch_path,
                        inline=(
                            "download" not in parse_qs(parsed.query)
                            and batch_path.suffix.lower() in _INLINE_SUFFIXES
                        ),
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
                if parsed.path == "/workspaces":
                    payload = self._read_json()
                    persist = payload.pop("persist", False)
                    profile_path = payload.pop("profile_path", None)
                    if not isinstance(persist, bool):
                        raise ValueError("persist must be a boolean")
                    if persist and (
                        not isinstance(profile_path, str)
                        or not profile_path.strip()
                    ):
                        raise ValueError(
                            "profile_path is required when persisting a workspace"
                        )
                    base_directory = (
                        Path(profile_path).expanduser().resolve().parent
                        if persist
                        else Path.cwd()
                    )
                    result = app.configure_workspace(
                        payload,
                        base_directory=base_directory,
                        persist_path=profile_path if persist else None,
                    )
                    self._write_json(201, result)
                    return
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
                if len(parts) == 3 and parts[0] == "batches" and parts[2] == "cancel":
                    self._write_json(200, app.cancel_batch(parts[1]))
                    return
                if len(parts) == 3 and parts[0] == "workspaces" and parts[2] == "batch":
                    payload = self._read_json()
                    job_id = app.start_batch(parts[1], str(payload.get("action", "")))
                    self._write_json(202, app.batch_status(job_id))
                    return
                if len(parts) == 5 and parts[0] == "workspaces" and parts[2] == "items" and parts[4] == "batch":
                    payload = self._read_json()
                    job_id = app.start_batch(parts[1], str(payload.get("action", "")), parts[3])
                    self._write_json(202, app.batch_status(job_id))
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
        source = app.batch_file(job_id, artifact["name"])
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
        saved.append(str(destination))
    saved_assets = []
    for name in app.batch_assets(job_id):
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(app.batch_file(job_id, name), destination)
        saved_assets.append(str(destination))
    result = {
        **status,
        "saved": saved,
        "saved_assets": saved_assets,
    }
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
    parser.add_argument("--action", help="Workspace batch action identifier")
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
