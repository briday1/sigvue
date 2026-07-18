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
from uuid import uuid4
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import parse_qs, urlparse

from plotly.offline import get_plotlyjs

from sigvue.catalog.browser import filter_items, paginate_items, search_items, sort_items
from sigvue.core.capabilities import Annotation, AnnotationRequest, ExportRequest
from sigvue.profile import WorkspaceLaunchSpec, load_browser_profile
from sigvue.registry.registry import WorkspaceRegistry
from sigvue.rendering import render_matplotlib_figure
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
    header { height:52px; display:flex; align-items:center; gap:14px; padding:0 22px; color:white; background:#102f3a; box-shadow:0 1px 6px #102f3a2b } .header-spacer { flex:1 } header select { min-height:30px; padding:3px 25px 3px 8px; color:#e7f1f3; background:#193741; border-color:#b9d0d54d; font-size:12px } header .sidebar-toggle { min-height:30px; padding:4px 10px; color:#e7f1f3; background:#193741; border-color:#b9d0d54d } header .icon-button { display:grid; width:34px; padding:4px; place-items:center } .icon-button svg { width:18px; height:18px; display:block } .fullscreen-toggle { border:1px solid #b9d0d54d; border-radius:5px; padding:3px 8px; background:transparent; color:#e7f1f3; font:18px/1 system-ui,sans-serif; cursor:pointer } .fullscreen-toggle:hover { background:#ffffff1c }
    header b { font-size:16px } header .home-title { all:unset; cursor:pointer; font:700 16px system-ui,sans-serif } header span { color:#b9d0d5; font-size:13px }
    main { width:min(1120px,calc(100% - 36px)); margin:34px auto 80px }
    main.item-page { width:calc(100% - 24px); max-width:none; margin:12px auto 0 }
    .crumb { color:var(--muted); margin-bottom:20px } .crumb button { all:unset; cursor:pointer; color:var(--accent) }
    h1 { margin:0 0 6px; font-size:30px; letter-spacing:-.02em } .lead { color:var(--muted); margin:0 0 28px }
    .toolbar { display:flex; gap:10px; margin:24px 0 } input,select { min-height:42px; border:1px solid #bdcbd0; border-radius:7px; padding:8px 12px; background:white; font:inherit }
    input[type=search] { flex:1 } button.primary { border:0; border-radius:7px; padding:10px 15px; color:white; background:var(--accent); font:600 14px inherit; cursor:pointer }
    .list { display:flex; flex-direction:column; gap:10px }
    .card { display:grid; grid-template-columns:minmax(180px,1fr) 2fr auto; align-items:center; gap:18px; border:1px solid var(--line); border-radius:8px; background:white; padding:16px 18px; box-shadow:0 2px 8px #17323c0b; cursor:pointer; transition:.15s }
    .card:hover { border-color:#8eb9bf; box-shadow:0 4px 14px #17323c14 } .card h2 { font-size:17px; margin:4px 0 } .card p { margin:0 } .card-tags { text-align:right; min-width:130px }
    .muted { color:var(--muted) } .tag { display:inline-block; border-radius:999px; padding:3px 9px; margin:2px 4px 2px 0; font-size:12px; background:#e8f3f3; color:#17626a }
    .data-toolbar { position:sticky; top:0; z-index:20; display:flex; align-items:center; gap:10px; min-height:46px; margin:0 -12px 4px; padding:6px 16px; background:#fbfcfcf2; border-bottom:1px solid var(--line); backdrop-filter:blur(8px) } .data-toolbar-spacer { flex:1 }
    .playback-bar { display:flex; align-items:center; gap:10px; flex:1; min-width:240px } .playback-bar .primary { padding:6px 10px; min-width:72px } .playback-track { position:relative; display:flex; flex:1; align-items:center; min-width:80px } .playback-track input[type=range] { position:relative; z-index:2; width:100%; min-height:0; padding:0 } .annotation-markers { position:absolute; z-index:0; inset:0 8px; pointer-events:none } .annotation-marker { position:absolute; top:20%; bottom:20%; width:1px; margin:0; padding:0; border:0; border-radius:0; background:var(--annotation-marker-color,#ffffff); box-shadow:none; opacity:.35; pointer-events:none } .annotation-marker.clustered { width:1px; margin:0; border:0; opacity:.55 } .playback-bar #current-time { flex:none; width:98px; min-height:30px; padding:4px 7px; text-align:right; font:12px ui-monospace,monospace } .playback-bar #counter { width:82px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap }
    .windowed-bar { display:flex; align-items:center; gap:8px; width:100%; min-width:0 } .windowed-track-stack { display:flex; flex:1; flex-direction:column; justify-content:center; height:34px; min-width:120px } .windowed-track-stack.has-overview { justify-content:flex-start; gap:1px } .windowed-label { height:9px; padding-left:2px; overflow:hidden; color:var(--muted); font-size:9px; line-height:9px; text-overflow:ellipsis; white-space:nowrap } .windowed-bar .windowed-time,.windowed-bar .windowed-width { flex:none; width:88px; min-height:30px; padding:4px 7px; text-align:right; font:12px ui-monospace,monospace } .windowed-total { flex:none; width:82px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap } .windowed-separator { flex:none; margin:0 2px 0 5px; color:var(--muted); font-size:11px } .windowed-width-label { display:flex; flex:none; align-items:center; gap:5px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap } .windowed-bar .windowed-width { width:76px } .windowed-track { position:relative; width:100%; height:30px; overflow:hidden; border:1px solid var(--line); border-radius:5px; background:var(--wash); touch-action:none } .windowed-track-stack.has-overview .windowed-track { height:24px } .windowed-overview { position:absolute; z-index:1; inset:2px; width:calc(100% - 4px); height:calc(100% - 4px); pointer-events:none } .windowed-selection { position:absolute; z-index:2; top:0; bottom:0; margin:0; padding:0; border:0; border-radius:0; background:color-mix(in srgb,var(--accent) 22%,transparent); cursor:grab } .windowed-selection:active { cursor:grabbing } .windowed-handle { position:absolute; z-index:3; top:0; bottom:0; width:9px; margin-left:-4px; padding:0; border:0; border-left:2px solid var(--accent); border-right:2px solid var(--accent); border-radius:1px; background:color-mix(in srgb,var(--accent) 45%,transparent); cursor:ew-resize } .windowed-selection:focus-visible,.windowed-handle:focus-visible { outline:2px solid var(--accent); outline-offset:-2px }
    .segmented-bar { display:flex; align-items:center; gap:10px; width:100%; min-width:0 } .segmented-bar > button { flex:none; min-height:30px; padding:4px 10px } .segmented-track { position:relative; flex:1; height:34px; min-width:160px; border:1px solid var(--line); border-radius:5px; background:var(--wash) } .segmented-track::before { position:absolute; top:50%; right:8px; left:8px; height:1px; background:var(--line); content:"" } .segment-marker { position:absolute; z-index:1; top:50%; width:12px; height:12px; margin:-6px 0 0 -6px; padding:0; border:2px solid var(--wash); border-radius:50%; background:var(--muted); box-shadow:0 0 0 1px var(--line); cursor:pointer; transform:scale(.85); transition:transform .12s,background .12s } .segment-marker:hover,.segment-marker:focus-visible { z-index:2; outline:2px solid var(--accent); outline-offset:2px; transform:scale(1.15) } .segment-marker.active { background:var(--accent); box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 35%,transparent); transform:scale(1.25) } .segment-count { flex:none; min-width:54px; color:var(--muted); font:12px ui-monospace,monospace; text-align:right; white-space:nowrap } .segment-time { flex:none; min-width:185px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap }
    .sidebar-toggle,.sidebar-close { border:1px solid var(--line); border-radius:6px; padding:5px 10px; background:white; color:var(--muted); font:600 12px inherit; cursor:pointer } .sidebar-toggle.has-view-parameters { color:var(--accent); border-color:var(--accent) } .workspace-sidebar { position:fixed; z-index:40; top:52px; right:0; bottom:0; display:flex; flex-direction:column; width:min(420px,calc(100vw - 20px)); padding:18px; overflow-y:auto; overflow-x:hidden; background:#fbfcfc; border-left:1px solid var(--line); box-shadow:-10px 0 30px #17323c1c; transform:translateX(102%); transition:transform .18s ease } .workspace-sidebar * { min-width:0 } .workspace-sidebar .table-wrap { overflow:visible; padding:8px 0 } .workspace-sidebar .data-table th,.workspace-sidebar .data-table td { white-space:normal; overflow-wrap:anywhere } .workspace-sidebar.open { transform:translateX(0) } .sidebar-backdrop { position:fixed; z-index:35; inset:52px 0 0; border:0; background:#102f3a24; opacity:0; pointer-events:none; transition:opacity .18s ease } .sidebar-backdrop.open { opacity:1; pointer-events:auto } .sidebar-head { display:flex; align-items:start; gap:12px; padding-bottom:16px; border-bottom:1px solid var(--line) } .sidebar-head .crumb { margin:0 0 7px; font-size:12px } .sidebar-title { min-width:0; flex:1 } .sidebar-title h1 { margin:0; font-size:20px; line-height:1.25 } .sidebar-title .subtitle { display:block; margin-top:4px; color:var(--muted); font-size:13px } .sidebar-close { flex:none; padding:4px 8px } .analysis-panel { display:flex; flex-direction:column; gap:16px; padding-top:16px } .analysis-panel h2 { margin:0; font-size:13px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em } .view-settings-empty { margin:8px 0 0; color:var(--muted); font-size:12px } .control-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px } .control-fields:empty { display:none } .control-fields label { display:flex; flex-direction:column; gap:3px; color:var(--muted); font-size:11px } .control-fields select,.control-fields input { min-height:34px; padding:5px 8px; color:var(--ink) } .control-fields select { padding-right:26px } .control-fields input[type=number] { width:100% } .control-fields input[type=color] { width:100%; padding:3px; cursor:pointer } .view-stats { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:7px 12px; margin:0; font-size:12px } .view-stats div { display:contents } .view-stats dt { color:var(--muted) } .view-stats dd { margin:0; text-align:right; color:var(--ink); font:12px ui-monospace,monospace; overflow-wrap:anywhere; white-space:normal }
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
    ::view-transition-old(root),::view-transition-new(root) { animation-duration:100ms; animation-timing-function:ease-out }
  </style>
</head>
<body><header><button class="home-title" id="app-home">__BROWSER_TITLE__</button><span id="app-subtitle">__BROWSER_SUBTITLE__</span><span class="header-spacer"></span><select id="theme-toggle" aria-label="Color theme"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select><details class="header-menu" id="header-annotate" hidden><summary>Annotate</summary><form class="header-popover" id="annotation-form"></form></details><details class="header-menu" id="header-download" hidden><summary>Download</summary><form class="header-popover" id="download-form"></form></details><button class="sidebar-toggle" id="header-details" data-sidebar-toggle aria-expanded="false" hidden>Details</button><button class="fullscreen-toggle" id="fullscreen-toggle" aria-label="Enter fullscreen" aria-pressed="false">⛶</button></header><main id="app"><div class="empty">Loading workspaces…</div></main>
<script src="/assets/plotly.min.js"></script>
<script>
const app=document.querySelector('#app');
const appHome=document.querySelector('#app-home');
const appSubtitle=document.querySelector('#app-subtitle');
const headerDetails=document.querySelector('#header-details');
const headerDownload=document.querySelector('#header-download');
const headerAnnotate=document.querySelector('#header-annotate');
const fullscreenToggle=document.querySelector('#fullscreen-toggle');
const themeToggle=document.querySelector('#theme-toggle');let themePreference=localStorage.getItem('sigvue-theme')||'system',activeThemeRefresh=null;
function resolvedTheme(){return themePreference==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):themePreference}function applyTheme(){document.documentElement.dataset.theme=resolvedTheme();themeToggle.value=themePreference}async function commitTheme(update){if(!document.startViewTransition){await update();return}const transition=document.startViewTransition(update);await transition.finished}async function refreshTheme(){if(activeThemeRefresh)await activeThemeRefresh();else applyTheme()}applyTheme();themeToggle.onchange=async()=>{themePreference=themeToggle.value;localStorage.setItem('sigvue-theme',themePreference);themeToggle.disabled=true;try{await refreshTheme()}catch(error){applyTheme();alert(`Theme refresh failed: ${error.message}`)}finally{themeToggle.disabled=false}};matchMedia('(prefers-color-scheme: dark)').addEventListener('change',async()=>{if(themePreference==='system'){try{await refreshTheme()}catch(error){applyTheme();console.error(error)}}});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=async path=>{const r=await fetch(path);if(!r.ok)throw new Error((await r.json()).detail||`Request failed (${r.status})`);return r.json()};
const apiPost=async(path,payload)=>{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(!r.ok)throw new Error((await r.json()).detail||`Request failed (${r.status})`);return r.json()};
const fail=e=>app.innerHTML=`<div class="error"><b>Unable to load this page</b><br>${esc(e.message)}</div>`;
let playbackTimer=null,playbackPosition=0,playbackPaused=false,playbackFollowLive=false,windowStart=0,windowEnd=null,segmentId=null,plotResizeObserver=null,windowOverviewResizeObserver=null,dataStageResizeFrame=null,annotations=[],annotationTimelineColorControl=null,activePlaybackSeek=null,activeAnnotationSeek=null;
const viewSelections={};
const timelineUnits={ns:{seconds:1e-9,label:'ns'},us:{seconds:1e-6,label:'µs'},ms:{seconds:1e-3,label:'ms'},s:{seconds:1,label:'s'},min:{seconds:60,label:'min'},h:{seconds:3600,label:'h'},d:{seconds:86400,label:'d'}};
function resolvedTimelineUnit(config){const requested=config.time_unit||'s';if(requested!=='auto')return requested;const duration=Math.abs(Number(config.duration_seconds)||0);if(duration>=172800)return'd';if(duration>=7200)return'h';if(duration>=120)return'min';if(duration>=1)return's';if(duration>=1e-3)return'ms';if(duration>=1e-6)return'us';return'ns'}
function timelineSpec(config){return timelineUnits[resolvedTimelineUnit(config)]||timelineUnits.s}
function displayTime(seconds,config){return Number(seconds)/timelineSpec(config).seconds}
function canonicalTime(value,config){return Number(value)*timelineSpec(config).seconds}
function timeBoxValue(seconds,config){const value=displayTime(seconds,config);return Number.isFinite(value)?Number(value.toPrecision(12)):0}
function formatTimelineTime(seconds,config){const value=displayTime(seconds,config),magnitude=Math.abs(value),digits=magnitude>=1000?2:magnitude>=100?3:magnitude>=1?6:9;return `${Number(value.toFixed(digits))} ${timelineSpec(config).label}`}
function choiceOptions(choices){return (choices||[]).map(choice=>`<option value="${esc(choice.value)}">${esc(choice.label)}</option>`).join('')}
function annotationFieldHtml(field){const required=field.required?'required':'',value=esc(field.default||'');if(field.field_type==='select')return `<label>${esc(field.label)}<select data-annotation-field="${esc(field.name)}" ${required}>${choiceOptions(field.options)}</select></label>`;if(field.field_type==='textarea')return `<label>${esc(field.label)}<textarea data-annotation-field="${esc(field.name)}" ${required}>${value}</textarea></label>`;return `<label>${esc(field.label)}<input ${field.field_type==='number'?'type="number" step="any"':''} data-annotation-field="${esc(field.name)}" value="${value}" ${required}></label>`}
function populatePlotBoundAnnotationFields(page){let populated=0;for(const field of page.annotation?.fields||[]){const binding=field.plot_binding;if(!binding)continue;const plot=[...document.querySelectorAll('[data-plot-view]')].find(candidate=>candidate.dataset.plotView===binding.view),range=plot?._fullLayout?.[binding.axis]?.range;if(!Array.isArray(range)||range.length<2)continue;const edges=range.map(Number).filter(Number.isFinite).sort((a,b)=>a-b),edge=binding.edge==='lower'?edges[0]:edges.at(-1),dynamicOffset=binding.offset_source==='playback'?playbackPosition:0,value=edge*Number(binding.scale??1)+Number(binding.offset??0)+dynamicOffset,input=[...document.querySelectorAll('[data-annotation-field]')].find(candidate=>candidate.dataset.annotationField===field.name);if(input&&Number.isFinite(value)){input.value=Number(value.toPrecision(12));populated++}}return populated}
function configureCapabilityMenus(page){const annotationForm=document.querySelector('#annotation-form'),downloadForm=document.querySelector('#download-form');annotationForm.innerHTML=(page.annotation?.fields||[]).map(annotationFieldHtml).join('')+'<button class="primary" type="submit">Add annotation</button>';headerAnnotate.ontoggle=()=>{if(headerAnnotate.open&&!populatePlotBoundAnnotationFields(page))setTimeout(()=>{if(headerAnnotate.open)populatePlotBoundAnnotationFields(page)},100)};downloadForm.innerHTML=`<label>Data<select id="export-scope">${choiceOptions(page.export?.scopes)}</select></label><label>Format<select id="export-format">${choiceOptions(page.export?.formats)}</select></label><button class="primary" type="submit">Download</button>`}
function markdown(value){return esc(value).replace(/^### (.+)$/gm,'<h3>$1</h3>').replace(/^## (.+)$/gm,'<h2>$1</h2>').replace(/^# (.+)$/gm,'<h1>$1</h1>').replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\n/g,'<br>')}
function plotlyFigure(figure,viewName){const id=`plotly-${encodeURIComponent(viewName)}`;return `<div id="${id}" class="plotly-view" data-plot-view="${esc(viewName)}"></div>`}
function matplotlibFigure(payload,viewName){return `<img class="matplotlib-view" data-matplotlib-view="${esc(viewName)}" alt="${esc(viewName)}" src="data:image/png;base64,${payload}">`}
const plotlyConfig={responsive:true,displaylogo:false};
function setPlotlyRuntime(started){const target=document.querySelector('[data-client-stat="plotly-runtime"]');if(target)target.textContent=`${(performance.now()-started).toFixed(1)} ms`}
async function initializePlotlyViews(views){const started=performance.now(),jobs=[];document.querySelectorAll('[data-plot-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.plotView);if(view&&view.kind==='plotly')jobs.push(Plotly.newPlot(target,view.value.data||[],view.value.layout||{},plotlyConfig))});await Promise.all(jobs);setPlotlyRuntime(started)}
async function updatePlotlyViews(views){const started=performance.now(),jobs=[];document.querySelectorAll('[data-plot-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.plotView);if(view&&view.kind==='plotly')jobs.push(Plotly.react(target,view.value.data||[],view.value.layout||{},plotlyConfig))});await Promise.all(jobs);setPlotlyRuntime(started)}
function updateMatplotlibViews(views){document.querySelectorAll('[data-matplotlib-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.matplotlibView);if(view&&view.kind==='matplotlib')target.src=`data:image/png;base64,${view.value}`})}
async function preloadMatplotlibViews(views){const sources=views.filter(view=>view.kind==='matplotlib').map(view=>`data:image/png;base64,${view.value}`);await Promise.all(sources.map(source=>new Promise(resolve=>{const image=new Image();image.onload=image.onerror=resolve;image.src=source;if(image.complete)resolve()})))}
function resizePlots(){document.querySelectorAll('[data-plot-view]').forEach(target=>Plotly.Plots.resize(target))}
function sizeDataStage(){const stage=document.querySelector('.data-stage');if(!stage)return;const available=Math.max(280,Math.floor(window.innerHeight-stage.getBoundingClientRect().top-4));stage.style.height=`${available}px`;cancelAnimationFrame(dataStageResizeFrame);dataStageResizeFrame=requestAnimationFrame(resizePlots)}
function observeDataStage(){plotResizeObserver?.disconnect();const stage=document.querySelector('.data-stage');if(!stage)return;plotResizeObserver=new ResizeObserver(()=>{cancelAnimationFrame(dataStageResizeFrame);dataStageResizeFrame=requestAnimationFrame(resizePlots)});plotResizeObserver.observe(stage);window.addEventListener('resize',sizeDataStage,{passive:true});sizeDataStage()}
function stopPlayback(){clearInterval(playbackTimer);playbackTimer=null;activePlaybackSeek=null;activeAnnotationSeek=null;plotResizeObserver?.disconnect();plotResizeObserver=null;windowOverviewResizeObserver?.disconnect();windowOverviewResizeObserver=null;cancelAnimationFrame(dataStageResizeFrame);dataStageResizeFrame=null;window.removeEventListener('resize',sizeDataStage)}
function syncFullscreenToggle(){const active=Boolean(document.fullscreenElement);fullscreenToggle.setAttribute('aria-label',active?'Exit fullscreen':'Enter fullscreen');fullscreenToggle.setAttribute('aria-pressed',String(active));fullscreenToggle.textContent=active?'×':'⛶';sizeDataStage()}
fullscreenToggle.onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen()}catch(e){/* Browser fullscreen can be unavailable in embedded contexts. */}};
document.addEventListener('fullscreenchange',syncFullscreenToggle);
function tableRows(value){if(Array.isArray(value))return value;if(!value||typeof value!=='object')return[];const columns=Object.keys(value),indices=[...new Set(columns.flatMap(column=>Object.keys(value[column]||{})))];return indices.map(index=>Object.fromEntries(columns.map(column=>[column,value[column]?.[index]])))}
function tableHtml(value){const rows=tableRows(value);if(!rows.length)return '<div class="empty">No rows</div>';const columns=[...new Set(rows.flatMap(row=>Object.keys(row)))];return `<div class="table-wrap"><table class="data-table"><thead><tr>${columns.map(column=>`<th>${esc(column)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${columns.map(column=>`<td>${esc(statText(row[column]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}
function renderValue(v){if(v.kind==='markdown')return `<article class="prose">${markdown(v.value)}</article>`;if(v.kind==='text')return `<div class="text-view">${esc(v.value)}</div>`;if(v.kind==='table'||v.kind==='dataframe')return tableHtml(v.value);return `<pre>${esc(typeof v.value==='string'?v.value:JSON.stringify(v.value,null,2))}</pre>`}
function renderView(v){if(v.kind==='plotly')return plotlyFigure(v.value,v.name);if(v.kind==='matplotlib')return matplotlibFigure(v.value,v.name);return `<div data-render-view="${esc(v.name)}">${renderValue(v)}</div>`}
function updateGenericViews(views){document.querySelectorAll('[data-render-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.renderView);if(view&&view.kind!=='plotly'&&view.kind!=='matplotlib')target.innerHTML=renderValue(view)})}
function gridTemplate(columns){if(Array.isArray(columns))return columns.map(weight=>`minmax(0,${Number(weight)||1}fr)`).join(' ');const count=Math.max(1,Number(columns)||1);return `repeat(${count},minmax(0,1fr))`}
function renderLayout(node,views,controls,values){if(node.kind==='view_slot'){const view=views.find(v=>v.name===node.view);return view?renderView(view):''}if(node.kind==='control_slot'){const control=controls.find(candidate=>candidate.name===node.props.name);return control?(control.control_type==='colormap'?colormapPickerHtml(control,values):`<label class="parameter-control">${esc(control.label||controlLabel(control.name))}${controlHtml(control,values)}</label>`):''}if(node.kind==='tabs'){const labels=node.children.map((child,i)=>child.props.label||`Tab ${i+1}`);return `<div class="layout-tabs" data-layout-tabs><nav class="tabs">${labels.map((label,i)=>`<button class="tab ${i===0?'active':''}" data-layout-tab="${i}">${esc(label)}</button>`).join('')}</nav><div class="layout-tab-panes">${node.children.map((child,i)=>`<div class="layout-tab-pane ${i===0?'active':''}" data-layout-pane="${i}" aria-hidden="${i!==0}">${renderLayout(child,views,controls,values)}</div>`).join('')}</div></div>`}if(node.kind==='view_switcher'){const key=String(node.props.key),selected=viewSelections[key]||0,labels=node.children.map((child,i)=>child.props.label||`View ${i+1}`),selector=node.props.selector||'buttons',control=selector==='dropdown'?`<select class="view-switcher-select" data-view-select>${labels.map((label,i)=>`<option value="${i}" ${i===selected?'selected':''}>${esc(label)}</option>`).join('')}</select>`:labels.map((label,i)=>`<button class="view-choice ${i===selected?'active':''}" data-view-choice="${i}">${esc(label)}</button>`).join('');return `<div class="view-switcher" data-view-switcher="${esc(key)}"><div class="view-switcher-head"><span class="view-switcher-label">${esc(node.props.label||'View')}</span>${control}</div>${node.children.map((child,i)=>`<div class="view-pane ${i===selected?'active':''}" data-view-pane="${i}" data-view-label="${esc(labels[i])}" aria-hidden="${i!==selected}">${renderLayout(child,views,controls,values)}</div>`).join('')}</div>`}const children=node.children.map(child=>renderLayout(child,views,controls,values)).join('');if(node.kind==='control_group')return `<div class="parameter-group" style="--parameter-columns:${Number(node.props.columns)||1}">${node.props.label?`<div class="parameter-group-title">${esc(node.props.label)}</div>`:''}${children}</div>`;if(node.kind==='grid'){const columnCount=Array.isArray(node.props.columns)?node.props.columns.length:Number(node.props.columns)||1,rowCount=Math.ceil(node.children.length/columnCount);return `<div class="playback-grid ${node.children.length===1?'single-plot':''}" style="--grid-template:${gridTemplate(node.props.columns)};--grid-rows:repeat(${rowCount},minmax(0,1fr));--grid-items:${node.children.length}">${node.children.map(child=>`<div class="channel">${renderLayout(child,views,controls,values)}</div>`).join('')}</div>`}if(node.kind==='column'||node.kind==='stack')return `<div class="layout-column">${children}</div>`;if(node.kind==='row')return `<div class="layout-row">${children}</div>`;if(node.kind==='panel')return `<div class="layout-panel">${children}</div>`;return children}
function bindLayoutTabs(){document.querySelectorAll('[data-layout-tabs]').forEach(root=>{const buttons=root.querySelectorAll(':scope > .tabs > [data-layout-tab]'),panes=root.querySelectorAll(':scope > .layout-tab-panes > [data-layout-pane]');buttons.forEach(button=>button.onclick=()=>{const selected=Number(button.dataset.layoutTab);buttons.forEach((candidate,index)=>candidate.classList.toggle('active',index===selected));panes.forEach((pane,index)=>{pane.classList.toggle('active',index===selected);pane.setAttribute('aria-hidden',String(index!==selected))});requestAnimationFrame(resizePlots)})})}
function bindViewSwitchers(){document.querySelectorAll('.view-switcher[data-view-switcher]').forEach(root=>{const activate=selected=>{viewSelections[root.dataset.viewSwitcher]=selected;root.querySelectorAll('[data-view-choice]').forEach((choice,index)=>choice.classList.toggle('active',index===selected));root.querySelectorAll(':scope > [data-view-pane]').forEach((pane,index)=>{pane.classList.toggle('active',index===selected);pane.setAttribute('aria-hidden',String(index!==selected))});const select=root.querySelector('[data-view-select]');if(select)select.value=selected};root.querySelectorAll('[data-view-choice]').forEach(button=>button.onclick=()=>activate(Number(button.dataset.viewChoice)));const select=root.querySelector('[data-view-select]');if(select)select.onchange=()=>activate(Number(select.value))})}
function annotationMarkerGroups(config,maximum=120){const duration=Math.max(0,Number(config.duration_seconds)||0);if(!duration)return[];const groups=new Map();for(const annotation of annotations){const position=Number(annotation.position_seconds);if(!Number.isFinite(position)||position<0||position>duration)continue;const bin=Math.min(maximum-1,Math.floor(position/duration*maximum));if(!groups.has(bin))groups.set(bin,[]);groups.get(bin).push(annotation)}return [...groups.values()]}
function annotationMarkerColor(){const control=[...document.querySelectorAll('[data-control]')].find(candidate=>candidate.dataset.control===annotationTimelineColorControl),color=control?.value;return /^#[0-9a-f]{6}$/i.test(color||'')?color:'#ffffff'}
function updateAnnotationMarkerColor(){const color=annotationMarkerColor();document.querySelectorAll('[data-annotation-markers]').forEach(target=>target.style.setProperty('--annotation-marker-color',color))}
function renderAnnotationMarkers(config){const duration=Math.max(0,Number(config.duration_seconds)||0),first=annotations[0],last=annotations.at(-1),signature=`${duration}|${annotations.length}|${first?.id||''}|${last?.id||''}`;let groups=null;document.querySelectorAll('[data-annotation-markers]').forEach(target=>{target.style.setProperty('--annotation-marker-color',annotationMarkerColor());if(target.dataset.annotationSignature===signature)return;target.dataset.annotationSignature=signature;groups??=annotationMarkerGroups(config);target.innerHTML=groups.map(group=>{const first=group[0],position=Number(first.position_seconds),percent=duration?position/duration*100:0,detail=[first.label||'Annotation',formatTimelineTime(position,config),first.comment].filter(Boolean).join(' · '),label=group.length===1?detail:`${group.length} annotations · ${formatTimelineTime(position,config)} · ${detail}`;return `<span class="annotation-marker ${group.length>1?'clustered':''}" style="left:${percent}%" data-annotation-position="${position}" data-annotation-count="${group.length}" aria-label="${esc(label)}" title="${esc(label)}"></span>`}).join('')})}
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
  windowOverviewResizeObserver?.disconnect();const root=document.querySelector('#windowed-bar'),track=root?.querySelector('#windowed-track');if(!root||!track)return;const duration=Number(config.duration_seconds)||0,minimum=Math.max(Number(config.minimum_window_seconds)||0,1e-12),step=Number(config.step_seconds)||minimum;
  if(windowEnd==null){windowStart=Number(config.window_start_seconds)||0;windowEnd=Number(config.window_end_seconds)||Math.min(duration,windowStart+minimum)}
  const canvas=track.querySelector('canvas'),selection=track.querySelector('#windowed-selection'),left=track.querySelector('#windowed-left'),right=track.querySelector('#windowed-right'),startInput=root.querySelector('#windowed-start'),endInput=root.querySelector('#windowed-end'),totalLabel=root.querySelector('#windowed-total'),widthInput=root.querySelector('#windowed-width'),unitLabel=root.querySelector('#windowed-unit');let drag=null,updating=false,pending=false,commitTimer=null;
  const clamp=()=>{windowStart=Math.min(duration-minimum,Math.max(0,Number(windowStart)||0));windowEnd=Math.min(duration,Math.max(windowStart+minimum,Number(windowEnd)||minimum))};
  const drawOverview=()=>{const rect=canvas.getBoundingClientRect(),ratio=devicePixelRatio||1,width=Math.max(1,Math.round(rect.width*ratio)),height=Math.max(1,Math.round(rect.height*ratio));if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height}const context=canvas.getContext('2d'),values=(config.overview_values||[]).map(Number).filter(Number.isFinite);context.clearRect(0,0,width,height);if(values.length<2)return;const limits=values.reduce((result,value)=>[Math.min(result[0],value),Math.max(result[1],value)],[Infinity,-Infinity]),low=limits[0],span=limits[1]-low||1,style=getComputedStyle(document.documentElement);context.beginPath();values.forEach((value,index)=>{const x=index/(values.length-1)*width,y=height-2-(value-low)/span*(height-4);if(index)context.lineTo(x,y);else context.moveTo(x,y)});context.strokeStyle=style.getPropertyValue('--accent').trim();context.lineWidth=Math.max(1,ratio);context.stroke()};
  const render=()=>{clamp();const spec=timelineSpec(config),leftPercent=duration?windowStart/duration*100:0,rightPercent=duration?windowEnd/duration*100:100;selection.style.left=`${leftPercent}%`;selection.style.width=`${rightPercent-leftPercent}%`;left.style.left=`${leftPercent}%`;right.style.left=`${rightPercent}%`;startInput.value=timeBoxValue(windowStart,config);endInput.value=timeBoxValue(windowEnd,config);startInput.max=endInput.max=displayTime(duration,config);widthInput.min=displayTime(minimum,config);widthInput.max=displayTime(duration,config);startInput.step=endInput.step=widthInput.step=displayTime(step,config);startInput.setAttribute('aria-label',`Window start time in ${spec.label}`);endInput.setAttribute('aria-label',`Window stop time in ${spec.label}`);widthInput.setAttribute('aria-label',`Window width in ${spec.label}`);totalLabel.textContent=`/ ${formatTimelineTime(duration,config)}`;widthInput.value=timeBoxValue(windowEnd-windowStart,config);unitLabel.textContent=`${spec.label} buffer`;left.setAttribute('aria-valuenow',String(windowStart));right.setAttribute('aria-valuenow',String(windowEnd));drawOverview();renderAnnotationMarkers(config)};
  const commit=async()=>{if(updating){pending=true;return}updating=true;try{await refresh()}finally{updating=false;if(pending){pending=false;void commit()}}};
  const scheduleCommit=()=>{if(commitTimer!==null)return;commitTimer=setTimeout(()=>{commitTimer=null;void commit()},75)};
  const finalCommit=()=>{if(commitTimer!==null){clearTimeout(commitTimer);commitTimer=null}void commit()};
  const begin=(kind,event)=>{event.preventDefault();drag={kind,pointer:event.pointerId,x:event.clientX,start:windowStart,end:windowEnd};track.setPointerCapture(event.pointerId)};
  left.onpointerdown=event=>begin('left',event);right.onpointerdown=event=>begin('right',event);selection.onpointerdown=event=>begin('move',event);
  track.onpointermove=event=>{if(!drag||drag.pointer!==event.pointerId)return;const delta=(event.clientX-drag.x)/Math.max(1,track.clientWidth)*duration;if(drag.kind==='left')windowStart=Math.min(drag.end-minimum,Math.max(0,drag.start+delta));else if(drag.kind==='right')windowEnd=Math.max(drag.start+minimum,Math.min(duration,drag.end+delta));else{const width=drag.end-drag.start;windowStart=Math.min(duration-width,Math.max(0,drag.start+delta));windowEnd=windowStart+width}render();scheduleCommit()};
  track.onpointerup=event=>{if(!drag||drag.pointer!==event.pointerId)return;drag=null;track.releasePointerCapture(event.pointerId);finalCommit()};track.onpointercancel=()=>{drag=null;finalCommit()};
  const editEndpoint=(kind,value)=>{const parsed=canonicalTime(value,config);if(!Number.isFinite(parsed)){render();return}if(kind==='start')windowStart=parsed;else windowEnd=parsed;render();finalCommit()};
  const editWidth=value=>{const parsed=canonicalTime(value,config);if(!Number.isFinite(parsed)||parsed<=0){render();return}const target=Math.min(duration,Math.max(minimum,parsed));windowEnd=windowStart+target;if(windowEnd>duration){windowEnd=duration;windowStart=Math.max(0,duration-target)}render();finalCommit()};
  activeAnnotationSeek=value=>{const position=Math.max(0,Math.min(duration,Number(value)||0)),width=windowEnd-windowStart;windowStart=Math.min(Math.max(0,duration-width),position);windowEnd=Math.min(duration,windowStart+width);render();finalCommit()};
  startInput.onchange=event=>editEndpoint('start',event.target.value);endInput.onchange=event=>editEndpoint('end',event.target.value);widthInput.onchange=event=>editWidth(event.target.value);startInput.onkeydown=endInput.onkeydown=widthInput.onkeydown=event=>{if(event.key==='Enter')event.currentTarget.blur()};
  const keyboard=(kind,event)=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const delta=event.key==='ArrowLeft'?-step:step;if(kind==='left')windowStart+=delta;else if(kind==='right')windowEnd+=delta;else{const width=windowEnd-windowStart;windowStart=Math.min(duration-width,Math.max(0,windowStart+delta));windowEnd=windowStart+width}render();void commit()};
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
function sidebarHtml(workspaceName,page){const details=page.controls.filter(control=>control.placement!=='inline'),groups=details.reduce((result,control)=>{const label=control.group||'Analysis settings';(result[label]??=[]).push(control);return result},{}),settings=Object.entries(groups).map(([label,controls])=>`<section><h2>${esc(label)}</h2>${controlGroupHtml(controls,page.control_values)}</section>`).join('');return `<button class="sidebar-backdrop" data-sidebar-backdrop aria-label="Close details"></button><aside class="workspace-sidebar" data-workspace-sidebar aria-label="Workspace details"><div class="sidebar-head"><div class="sidebar-title"><div class="crumb"><button id="home">Workspaces</button> / <button id="back">${esc(workspaceName)}</button></div><h1>${esc(page.title)}</h1><span class="subtitle">${esc(page.subtitle||'')}</span></div><button class="sidebar-close" data-sidebar-close aria-label="Close details">Close</button></div><div class="analysis-panel">${settings}<section><h2>View details</h2><dl class="view-stats" id="view-stats">${statisticsRows(page.statistics)}</dl></section><section><h2>Runtime</h2><dl class="view-stats" id="runtime-stats">${statisticsRows(page.runtime_statistics)}<div><dt>Plotly render</dt><dd data-client-stat="plotly-runtime">—</dd></div></dl></section></div></aside>`}
function updateStatistics(statistics,runtimeStatistics){const viewTarget=document.querySelector('#view-stats'),runtimeTarget=document.querySelector('#runtime-stats');if(viewTarget)viewTarget.innerHTML=statisticsRows(statistics);if(runtimeTarget)runtimeTarget.innerHTML=`${statisticsRows(runtimeStatistics)}<div><dt>Plotly render</dt><dd data-client-stat="plotly-runtime">—</dd></div>`}
function bindSidebar(){const sidebar=document.querySelector('[data-workspace-sidebar]'),backdrop=document.querySelector('[data-sidebar-backdrop]'),toggle=document.querySelector('[data-sidebar-toggle]');if(!sidebar||!backdrop||!toggle)return;const setOpen=open=>{sidebar.classList.toggle('open',open);backdrop.classList.toggle('open',open);toggle.setAttribute('aria-expanded',String(open))};toggle.onclick=()=>setOpen(!sidebar.classList.contains('open'));backdrop.onclick=()=>setOpen(false);sidebar.querySelector('[data-sidebar-close]').onclick=()=>setOpen(false)}
async function catalog(navigate=true){stopPlayback();activeThemeRefresh=null;headerDetails.hidden=true;headerDownload.hidden=true;headerDownload.open=false;headerAnnotate.hidden=true;headerAnnotate.open=false;app.className='';if(navigate)history.pushState(null,'','/');try{const initial=await api('/workspaces'),workspaces=initial.workspaces;app.innerHTML=`<h1>Workspaces</h1><p class="lead">Choose a workspace to discover its available items.</p><div class="toolbar"><input id="workspace-search" type="search" placeholder="Search workspaces…" aria-label="Search workspaces"></div><div class="list" id="workspaces"></div>`;const draw=()=>{const q=document.querySelector('#workspace-search').value.toLowerCase().trim();const shown=workspaces.filter(w=>!q||`${w.name} ${w.description||''} ${w.category||''} ${(w.tags||[]).join(' ')} ${w.id}`.toLowerCase().includes(q));document.querySelector('#workspaces').innerHTML=shown.length?shown.map(w=>`<article class="card" data-id="${esc(w.id)}"><div><span class="tag">${esc(w.category||'workspace')}</span><h2>${esc(w.name)}</h2></div><p class="muted">${esc(w.description)}</p><div class="card-tags">${w.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div></article>`).join(''):'<div class="empty">No matching workspaces.</div>';document.querySelectorAll('[data-id]').forEach(x=>x.onclick=()=>items(x.dataset.id,workspaces.find(w=>w.id===x.dataset.id).name))};draw();document.querySelector('#workspace-search').oninput=draw}catch(e){fail(e)}}
async function items(id,name,navigate=true,directory=[]){stopPlayback();activeThemeRefresh=null;headerDetails.hidden=true;headerDownload.hidden=true;headerDownload.open=false;headerAnnotate.hidden=true;headerAnnotate.open=false;app.className='';const route=directory.length?`/workspace/${encodeURIComponent(id)}/browse/${directory.map(encodeURIComponent).join('/')}`:`/workspace/${encodeURIComponent(id)}`;if(navigate)history.pushState(null,'',route);app.innerHTML='<div class="empty">Discovering items…</div>';try{const params=new URLSearchParams();directory.forEach(segment=>params.append('directory',segment));const listing=await api(`/workspaces/${encodeURIComponent(id)}/items?${params}`),list=listing.items,folders=listing.directories,crumbs=directory.map((segment,index)=>` / <button data-directory-level="${index+1}">${esc(segment)}</button>`).join('');app.innerHTML=`<div class="crumb"><button id="home">Workspaces</button> / <button id="workspace-root">${esc(name)}</button>${crumbs}</div><h1>${esc(directory.at(-1)||name)}</h1><p class="lead">Browse and open discovered items.</p><div class="toolbar"><input id="search" type="search" placeholder="Search this folder…"></div><div class="list" id="items"></div>`;const draw=()=>{const q=document.querySelector('#search').value.toLowerCase(),shownFolders=folders.filter(folder=>!q||folder.name.toLowerCase().includes(q)),shown=list.filter(x=>!q||`${x.title} ${x.subtitle||''} ${x.tags.join(' ')}`.toLowerCase().includes(q)),folderRows=shownFolders.map((folder,index)=>`<article class="card" data-folder="${folders.indexOf(folder)}"><div><span class="tag">folder</span><h2>${esc(folder.name)}</h2></div><p class="muted">Open directory</p><div class="card-tags"></div></article>`),itemRows=shown.map(x=>`<article class="card" data-item="${esc(x.id)}"><div><h2>${esc(x.title)}</h2></div><p class="muted">${esc(x.subtitle||'')}</p><div class="card-tags">${x.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div></article>`);document.querySelector('#items').innerHTML=folderRows.length||itemRows.length?[...folderRows,...itemRows].join(''):'<div class="empty">No matching items.</div>';document.querySelectorAll('[data-folder]').forEach(element=>element.onclick=()=>items(id,name,true,folders[Number(element.dataset.folder)].path));document.querySelectorAll('[data-item]').forEach(element=>element.onclick=()=>openItem(id,name,element.dataset.item))};draw();document.querySelector('#home').onclick=()=>catalog();document.querySelector('#workspace-root').onclick=()=>items(id,name,true,[]);document.querySelectorAll('[data-directory-level]').forEach(element=>element.onclick=()=>items(id,name,true,directory.slice(0,Number(element.dataset.directoryLevel))));document.querySelector('#search').oninput=draw}catch(e){fail(e)}}
async function openItem(wid,wname,iid,navigate=true,controlValues={},preservePlayback=false){
  stopPlayback();activeThemeRefresh=null;headerDetails.hidden=true;headerDownload.hidden=true;headerDownload.open=false;headerAnnotate.hidden=true;headerAnnotate.open=false;app.className='item-page';if(!preservePlayback){playbackPosition=0;playbackPaused=false;playbackFollowLive=false;windowStart=0;windowEnd=null;segmentId=null;Object.keys(viewSelections).forEach(key=>delete viewSelections[key])}if(navigate)history.pushState(null,'',`/workspace/${encodeURIComponent(wid)}/item/${encodeURIComponent(iid)}`);app.innerHTML='<div class="empty">Opening item…</div>';
  try{const request=async values=>api(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}?${new URLSearchParams(values)}`),windowValues=()=>windowEnd==null?{}:{__window_start_seconds:windowStart,__window_end_seconds:windowEnd},segmentValues=()=>segmentId==null?{}:{__segment_id:segmentId};let data=await request({...controlValues,...windowValues(),...segmentValues(),__theme:resolvedTheme(),__playback_time_seconds:playbackPosition}),p=data.page,requestGeneration=0;const isPlayback=['seek','live'].includes(p.playback.mode),isWindowed=p.playback.mode==='windowed',isSegmented=p.playback.mode==='segmented';annotations=p.annotation?.entries||[];
    const playbackConfig=p.playback;annotationTimelineColorControl=p.annotation?.timeline_color_control||null;if(isWindowed&&windowEnd==null){windowStart=Number(playbackConfig.window_start_seconds)||0;windowEnd=Number(playbackConfig.window_end_seconds)||playbackConfig.duration_seconds}if(isSegmented)segmentId=playbackConfig.selected_segment_id;headerDetails.hidden=false;headerDownload.hidden=!p.export?.enabled;headerAnnotate.hidden=!p.annotation?.enabled;configureCapabilityMenus(p);const hasWindowOverview=(playbackConfig.overview_values||[]).length>0,playbackToolbar=isPlayback?`<div class="data-toolbar"><div class="playback-bar" id="playback-bar"><button class="primary" id="toggle">${playbackPaused?'▶ Play':'❚❚ Pause'}</button><div class="playback-track"><input id="position" aria-label="Playback position" type="range" min="0" value="${playbackPosition}"><div class="annotation-markers" data-annotation-markers aria-label="Annotations"></div></div><input id="current-time" aria-label="Current playback time in seconds" type="number" min="0" step="any" value="${playbackPosition}"><span id="counter"></span>${playbackConfig.mode==='live'?'<button class="live-toggle" id="jump-live">Live</button>':''}</div></div>`:isWindowed?`<div class="data-toolbar"><div class="windowed-bar" id="windowed-bar"><input class="windowed-time" id="windowed-start" aria-label="Window start time in seconds" type="number" min="0" max="${playbackConfig.duration_seconds}" step="any"><div class="windowed-track-stack ${hasWindowOverview?'has-overview':''}">${hasWindowOverview?`<span class="windowed-label" title="${esc(playbackConfig.overview_label||'Signal overview')}">${esc(playbackConfig.overview_label||'Signal overview')}</span>`:''}<div class="windowed-track" id="windowed-track"><canvas class="windowed-overview" aria-hidden="true"></canvas><div class="annotation-markers" data-annotation-markers aria-label="Annotations"></div><button class="windowed-selection" id="windowed-selection" type="button" aria-label="Move selected window"></button><button class="windowed-handle" id="windowed-left" type="button" role="slider" aria-label="Window start" aria-valuemin="0" aria-valuemax="${playbackConfig.duration_seconds}"></button><button class="windowed-handle" id="windowed-right" type="button" role="slider" aria-label="Window end" aria-valuemin="0" aria-valuemax="${playbackConfig.duration_seconds}"></button></div></div><input class="windowed-time" id="windowed-end" aria-label="Window stop time in seconds" type="number" min="0" max="${playbackConfig.duration_seconds}" step="any"><span class="windowed-total" id="windowed-total"></span><span class="windowed-separator" aria-hidden="true">•</span><label class="windowed-width-label"><input class="windowed-width" id="windowed-width" aria-label="Window buffer width in seconds" type="number" min="${playbackConfig.minimum_window_seconds}" max="${playbackConfig.duration_seconds}" step="any"> <span id="windowed-unit">s buffer</span></label></div></div>`:isSegmented?`<div class="data-toolbar"><div class="segmented-bar" id="segmented-bar"><button id="segment-previous" type="button">Previous</button><div class="segmented-track" id="segmented-track" aria-label="Available result segments"></div><span class="segment-count" id="segment-count"></span><span class="segment-time" id="segment-time"></span><button id="segment-next" type="button">Next</button></div></div>`:'';app.innerHTML=`${playbackToolbar}${sidebarHtml(wname,p)}<section class="data-stage"><div id="active-view" class="view"></div></section>`;
    document.querySelector('#active-view').innerHTML=renderLayout(p.layout,p.rendered_views,p.controls,p.control_values);bindLayoutTabs();bindViewSwitchers();bindColormapPickers();bindLimitsPickers();bindSidebar();observeDataStage();void initializePlotlyViews(p.rendered_views);requestAnimationFrame(resizePlots);
    const selected=()=>({...Object.fromEntries([...document.querySelectorAll('[data-control]')].map(c=>[c.dataset.control,c.type==='checkbox'?String(c.checked):c.value])),...windowValues(),...segmentValues(),__theme:resolvedTheme(),__playback_follow_live:playbackFollowLive});
    const annotationPosition=()=>isWindowed?windowStart:isSegmented?Number((playbackConfig.segments||[]).find(segment=>segment.identifier===segmentId)?.start_seconds||0):playbackPosition;
    const annotationDuration=()=>isWindowed?windowEnd-windowStart:isSegmented?Number((playbackConfig.segments||[]).find(segment=>segment.identifier===segmentId)?.duration_seconds||0):null;
    document.querySelector('#annotation-form').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget,button=form.querySelector('button'),values=Object.fromEntries([...form.querySelectorAll('[data-annotation-field]')].map(field=>[field.dataset.annotationField,field.value]));button.disabled=true;button.textContent='Saving…';try{const result=await apiPost(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}/annotations`,{control_values:{...selected(),__playback_time_seconds:playbackPosition},position_seconds:annotationPosition(),duration_seconds:annotationDuration(),values});annotations.push(result);renderAnnotationMarkers(playbackConfig);headerAnnotate.open=false;form.reset();try{await refresh(true)}catch(refreshError){alert(`Annotation saved, but the plots could not refresh: ${refreshError.message}`)}}catch(error){alert(`Annotation failed: ${error.message}`)}finally{button.disabled=false;button.textContent='Add annotation'}};
    document.querySelector('#download-form').onsubmit=async event=>{event.preventDefault();const button=event.currentTarget.querySelector('button'),scope=document.querySelector('#export-scope').value,format=document.querySelector('#export-format').value;button.disabled=true;button.textContent='Preparing…';try{const job=await apiPost(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}/exports`,{control_values:{...selected(),__playback_time_seconds:playbackPosition},scope,format});let status;do{await new Promise(resolve=>setTimeout(resolve,350));status=await api(job.status_url)}while(status.status==='pending'||status.status==='running');if(status.status==='error')throw new Error(status.detail);for(const file of status.files){const link=document.createElement('a');link.href=file.url;link.download=file.name;link.click()}headerDownload.open=false}catch(error){alert(`Export failed: ${error.message}`)}finally{button.disabled=false;button.textContent='Download'}};
    const refresh=async(includeStatic=false,commitRequestedTheme=false)=>{const generation=++requestGeneration,result=await request({...selected(),__playback_time_seconds:playbackPosition,__include_static_views:includeStatic});if(generation!==requestGeneration)return false;data=result;Object.assign(playbackConfig,result.page.playback);p=result.page;p.playback=playbackConfig;annotationTimelineColorControl=p.annotation?.timeline_color_control||null;if(Array.isArray(p.annotation?.entries))annotations=p.annotation.entries;if(playbackFollowLive)playbackPosition=playbackConfig.duration_seconds;if(commitRequestedTheme)await preloadMatplotlibViews(p.rendered_views);const render=async()=>{if(commitRequestedTheme)applyTheme();updateStatistics(p.statistics,p.runtime_statistics);await updatePlotlyViews(p.rendered_views);updateMatplotlibViews(p.rendered_views);updateGenericViews(p.rendered_views)};if(commitRequestedTheme)await commitTheme(render);else await render();return true};activeThemeRefresh=async()=>{if(isPlayback)clearInterval(playbackTimer);const applied=await refresh(true,true);if(isPlayback&&applied)startFrameworkPlayback(p.playback,refresh);else if(isWindowed&&applied)startFrameworkWindowed(p.playback,refresh);else if(isSegmented&&applied)startFrameworkSegmented(p.playback,refresh)};
    const settingsChanged=async()=>{if(isPlayback)clearInterval(playbackTimer);const applied=await refresh(true);if(isPlayback&&applied)startFrameworkPlayback(p.playback,refresh);else if(isWindowed&&applied)startFrameworkWindowed(p.playback,refresh);else if(isSegmented&&applied)startFrameworkSegmented(p.playback,refresh)};
    if(isPlayback)startFrameworkPlayback(p.playback,refresh);else if(isWindowed)startFrameworkWindowed(p.playback,refresh);else if(isSegmented)startFrameworkSegmented(p.playback,refresh);else if(p.refresh.enabled)startFrameworkRefresh(p.refresh,refresh);document.querySelectorAll('[data-control]').forEach(x=>{x.onchange=settingsChanged;if(x.type==='color')x.oninput=()=>{const swatch=x.closest('[data-style-picker]')?.querySelector('[data-style-swatch]');if(swatch)swatch.style.background=x.value;updateAnnotationMarkerColor()}});document.querySelector('#home').onclick=()=>catalog();document.querySelector('#back').onclick=()=>items(wid,wname,true,data.item.navigation_path||[])
  }catch(e){fail(e)}}
async function boot(){const parts=location.pathname.split('/').filter(Boolean).map(decodeURIComponent);if(parts[0]!=='workspace')return catalog(false);try{const {workspaces}=await api('/workspaces'),workspace=workspaces.find(w=>w.id===parts[1]);if(!workspace)return catalog(false);if(parts[2]==='item'&&parts[3])return openItem(workspace.id,workspace.name,parts[3],false);if(parts[2]==='browse')return items(workspace.id,workspace.name,false,parts.slice(3));return items(workspace.id,workspace.name,false)}catch(e){fail(e)}}
appHome.onclick=()=>catalog();
window.onpopstate=boot;boot();
</script></body></html>"""


@dataclass(frozen=True)
class WorkspaceModuleRegistration:
    module_name: str
    attribute: str
    watch_path: Path | None = None
    configuration: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExportJob:
    directory: Path
    future: Future[dict[str, object]]


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
            "directories": [
                {"name": name, "path": [*directory, name]}
                for name in child_names
            ],
            "items": [_item_payload(item) for item in paged],
        }

    def open_item(self, workspace_id: str, item_id: str, control_values: dict[str, object] | None = None) -> dict[str, Any]:
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
        for view in opened.page.views:
            if view.update_policy == "static" and not include_static:
                continue
            value = view.callback(values)
            render_kind = detect_render_kind(value)
            kind = render_kind.value
            if render_kind == RenderKind.MATPLOTLIB:
                value = render_matplotlib_figure(value)
            rendered_views.append(
                {"name": view.name, "kind": kind, "value": _json_value(value), "update": view.update_policy}
            )
        statistics = dict(opened.page.statistics)
        runtime_statistics = {}
        analysis_runtime = statistics.pop("Analysis runtime", None)
        if analysis_runtime is not None:
            runtime_statistics["Analysis runtime"] = analysis_runtime
        runtime_statistics["View callbacks"] = f"{(time.perf_counter() - callbacks_started) * 1_000:.1f} ms"
        annotation = opened.page.annotation
        export = opened.page.export
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
                    "entries": (
                        [_annotation_payload(entry) for entry in annotation.discover_callback()]
                        if annotation and include_static
                        else None
                    ),
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
        result = capability.annotate_callback(
            requested_values,
            AnnotationRequest(position_seconds=position, duration_seconds=duration, values=supplied),
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
                raise ValueError("DataExporter.export() must return a file created in its destination directory")
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
        raise TypeError("DataAnnotator must return Annotation values")
    return {
        "id": annotation.identifier,
        "position_seconds": annotation.start_seconds,
        "duration_seconds": annotation.duration_seconds,
        "label": annotation.label,
        "comment": annotation.comment,
        "frequency_lower_hz": annotation.frequency_lower_hz,
        "frequency_upper_hz": annotation.frequency_upper_hz,
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
    return WorkspaceModuleRegistration(spec.module_name, spec.attribute, spec.watch_path, spec.configuration)


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

        def _write_export_file(self, path: Path) -> None:
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
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
                    app.reload_workspace_modules()
                    self._write_json(200, {
                        "workspaces": app.list_workspaces(),
                        "title": app.title,
                        "subtitle": app.subtitle,
                    })
                except Exception as exc:
                    self._write_json(500, {"error": "browser_profile_reload_failed", "detail": str(exc)})
                return

            parts = [segment for segment in parsed.path.split("/") if segment]
            try:
                if len(parts) == 2 and parts[0] == "exports":
                    self._write_json(200, app.export_status(parts[1]))
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
            parts = [segment for segment in parsed.path.split("/") if segment]
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Sigvue server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--config", type=Path, help="Load workspace selection and data settings from browser.toml")
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
