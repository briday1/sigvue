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
from datetime import datetime, timezone
from html import escape as html_escape
import importlib
import inspect
import json
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

from workspace_browser.catalog.browser import filter_items, paginate_items, search_items, sort_items
from workspace_browser.profile import WorkspaceLaunchSpec, load_browser_profile
from workspace_browser.registry.registry import WorkspaceRegistry
from workspace_browser.rendering import render_matplotlib_figure
from workspace_browser.rendering.dispatch import RenderKind, detect_render_kind
from workspace_browser.web.mat_export import write_mat_export
from workspace_browser.web.png_export import write_png_bundle


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
    .playback-bar { display:flex; align-items:center; gap:10px; flex:1; min-width:240px } .playback-bar .primary { padding:6px 10px; min-width:72px } .playback-track { position:relative; display:flex; flex:1; align-items:center; min-width:80px } .playback-track input[type=range] { width:100%; min-height:0; padding:0 } .log-markers { position:absolute; z-index:2; left:8px; right:8px; top:50%; height:0; pointer-events:none } .log-marker { position:absolute; width:4px; height:15px; margin:-7px 0 0 -2px; padding:0; border:1px solid #fff9; border-radius:2px; background:#d35d35; box-shadow:0 0 0 1px #6f2d1d55; cursor:pointer; pointer-events:auto; transform:scaleY(.82); transition:transform .12s,background .12s } .log-marker:hover,.log-marker:focus-visible { z-index:2; background:#f07a4e; outline:2px solid var(--accent); outline-offset:2px; transform:scaleY(1.12) } .playback-bar #current-time { flex:none; width:98px; min-height:30px; padding:4px 7px; text-align:right; font:12px ui-monospace,monospace } .playback-bar #counter { width:82px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap }
    .windowed-bar { display:flex; align-items:center; gap:10px; width:100%; min-width:0 } .windowed-label { flex:none; max-width:140px; overflow:hidden; color:var(--muted); font-size:12px; text-overflow:ellipsis; white-space:nowrap } .windowed-bar .windowed-time { flex:none; width:88px; min-height:30px; padding:4px 7px; text-align:right; font:12px ui-monospace,monospace } .windowed-total { flex:none; width:82px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap } .windowed-track { position:relative; flex:1; height:34px; min-width:120px; overflow:hidden; border:1px solid var(--line); border-radius:5px; background:var(--wash); touch-action:none } .windowed-overview { position:absolute; inset:2px; width:calc(100% - 4px); height:calc(100% - 4px) } .windowed-selection { position:absolute; z-index:2; top:0; bottom:0; margin:0; padding:0; border:0; border-radius:0; background:color-mix(in srgb,var(--accent) 22%,transparent); cursor:grab } .windowed-selection:active { cursor:grabbing } .windowed-handle { position:absolute; z-index:3; top:0; bottom:0; width:9px; margin-left:-4px; padding:0; border:0; border-left:2px solid var(--accent); border-right:2px solid var(--accent); border-radius:1px; background:color-mix(in srgb,var(--accent) 45%,transparent); cursor:ew-resize } .windowed-selection:focus-visible,.windowed-handle:focus-visible { outline:2px solid var(--accent); outline-offset:-2px }
    .segmented-bar { display:flex; align-items:center; gap:10px; width:100%; min-width:0 } .segmented-bar > button { flex:none; min-height:30px; padding:4px 10px } .segmented-track { position:relative; flex:1; height:34px; min-width:160px; border:1px solid var(--line); border-radius:5px; background:var(--wash) } .segmented-track::before { position:absolute; top:50%; right:8px; left:8px; height:1px; background:var(--line); content:"" } .segment-marker { position:absolute; z-index:1; top:50%; width:12px; height:12px; margin:-6px 0 0 -6px; padding:0; border:2px solid var(--wash); border-radius:50%; background:var(--muted); box-shadow:0 0 0 1px var(--line); cursor:pointer; transform:scale(.85); transition:transform .12s,background .12s } .segment-marker:hover,.segment-marker:focus-visible { z-index:2; outline:2px solid var(--accent); outline-offset:2px; transform:scale(1.15) } .segment-marker.active { background:var(--accent); box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 35%,transparent); transform:scale(1.25) } .segment-count { flex:none; min-width:54px; color:var(--muted); font:12px ui-monospace,monospace; text-align:right; white-space:nowrap } .segment-time { flex:none; min-width:185px; color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap }
    .sidebar-toggle,.sidebar-close { border:1px solid var(--line); border-radius:6px; padding:5px 10px; background:white; color:var(--muted); font:600 12px inherit; cursor:pointer } .sidebar-toggle.has-view-parameters { color:var(--accent); border-color:var(--accent) } .workspace-sidebar { position:fixed; z-index:40; top:52px; right:0; bottom:0; display:flex; flex-direction:column; width:min(420px,calc(100vw - 20px)); padding:18px; overflow-y:auto; overflow-x:hidden; background:#fbfcfc; border-left:1px solid var(--line); box-shadow:-10px 0 30px #17323c1c; transform:translateX(102%); transition:transform .18s ease } .workspace-sidebar * { min-width:0 } .workspace-sidebar .table-wrap { overflow:visible; padding:8px 0 } .workspace-sidebar .data-table th,.workspace-sidebar .data-table td { white-space:normal; overflow-wrap:anywhere } .workspace-sidebar.open { transform:translateX(0) } .sidebar-backdrop { position:fixed; z-index:35; inset:52px 0 0; border:0; background:#102f3a24; opacity:0; pointer-events:none; transition:opacity .18s ease } .sidebar-backdrop.open { opacity:1; pointer-events:auto } .sidebar-head { display:flex; align-items:start; gap:12px; padding-bottom:16px; border-bottom:1px solid var(--line) } .sidebar-head .crumb { margin:0 0 7px; font-size:12px } .sidebar-title { min-width:0; flex:1 } .sidebar-title h1 { margin:0; font-size:20px; line-height:1.25 } .sidebar-title .subtitle { display:block; margin-top:4px; color:var(--muted); font-size:13px } .sidebar-close { flex:none; padding:4px 8px } .analysis-panel { display:flex; flex-direction:column; gap:16px; padding-top:16px } .analysis-panel h2 { margin:0; font-size:13px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em } .view-settings-empty { margin:8px 0 0; color:var(--muted); font-size:12px } .control-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px } .control-fields:empty { display:none } .control-fields label { display:flex; flex-direction:column; gap:3px; color:var(--muted); font-size:11px } .control-fields select,.control-fields input { min-height:34px; padding:5px 8px; color:var(--ink) } .control-fields select { padding-right:26px } .control-fields input[type=number] { width:100% } .control-fields input[type=color] { width:100%; padding:3px; cursor:pointer } .view-stats { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:7px 12px; margin:0; font-size:12px } .view-stats div { display:contents } .view-stats dt { color:var(--muted) } .view-stats dd { margin:0; text-align:right; color:var(--ink); font:12px ui-monospace,monospace; overflow-wrap:anywhere; white-space:normal }
    .style-picker-list { display:flex; flex-direction:column; gap:7px; margin-top:9px } .style-picker { overflow:hidden; border:1px solid var(--line); border-radius:8px; background:white } html[data-theme="dark"] .style-picker { background:#193741 } .style-picker summary { display:flex; align-items:center; gap:9px; min-height:40px; padding:7px 10px; cursor:pointer; list-style:none; user-select:none } .style-picker summary::-webkit-details-marker { display:none } .style-picker summary::after { content:'⌄'; margin-left:auto; color:var(--muted); font-size:16px; transition:transform .15s } .style-picker[open] summary::after { transform:rotate(180deg) } .style-swatch { flex:none; width:18px; height:18px; border:2px solid #ffffffcc; border-radius:50%; box-shadow:0 0 0 1px #13212b2e } .style-picker-name { min-width:0; overflow:hidden; color:var(--ink); font-size:13px; font-weight:650; text-overflow:ellipsis; white-space:nowrap } .style-picker-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px 12px; padding:10px; border-top:1px solid var(--line); background:var(--wash) } .style-picker-fields label { display:flex; flex-direction:column; gap:3px; color:var(--muted); font-size:11px } .style-picker-fields input,.style-picker-fields select { width:100%; min-height:34px; padding:5px 8px; color:var(--ink) } .style-picker-fields input[type=color] { padding:3px; cursor:pointer }
    .data-stage { height:400px; min-height:0; overflow:hidden } #active-view,.view { height:100%; min-height:0 } .layout-tabs { position:relative; display:flex; flex-direction:column; height:100%; min-height:0 } .layout-tab-panes { position:relative; flex:1; min-height:0 } .tabs { flex:none; display:flex; gap:4px; overflow-x:auto; border-bottom:1px solid var(--line); margin:0 0 4px; padding-left:4px } .tab { flex:none; border:0; border-bottom:3px solid transparent; background:none; padding:9px 13px 7px; color:var(--muted); font:600 13px inherit; cursor:pointer } .tab.active { color:var(--accent); border-color:var(--accent) } .layout-tab-pane { width:100%; height:100%; min-height:0 } .layout-tab-pane:not(.active) { position:absolute; inset:0; visibility:hidden; pointer-events:none }
    .view h1 { font-size:20px } .view h2 { font-size:16px } .plotly-view { width:100%; height:100%; min-height:0 } .matplotlib-view { display:block; width:100%; height:100%; min-height:0; object-fit:contain; background:white } .playback-grid { display:grid; width:100%; height:100%; min-height:0; grid-template-columns:var(--grid-template,repeat(2,minmax(0,1fr))); grid-template-rows:var(--grid-rows,minmax(0,1fr)); gap:4px } .playback-grid.single-plot { display:block; height:100% } .single-plot .channel { width:100%; height:100%; border:0 } .view-switcher { position:relative; display:flex; flex-direction:column; height:100%; min-height:0 } .view-pane { width:100%; flex:1; min-height:0 } .view-pane:not(.active) { position:absolute; inset:40px 0 0; visibility:hidden; pointer-events:none } .view-switcher-head { position:relative; z-index:2; flex:none; height:40px; display:flex; align-items:center; gap:10px; padding:4px 8px; border-bottom:1px solid var(--line); background:white } .view-switcher-label { color:var(--muted); font-size:12px; font-weight:600 } .view-choice { border:1px solid var(--line); border-radius:5px; background:white; padding:4px 9px; color:var(--muted); font:12px inherit; cursor:pointer } .view-choice.active { border-color:var(--accent); background:#e8f3f3; color:#17626a } .view-switcher-select { min-height:30px; min-width:150px; padding:4px 28px 4px 8px; border:1px solid var(--line); border-radius:5px; background:white; color:var(--ink); font:12px inherit } .parameter-group { flex:none; display:grid; grid-template-columns:repeat(var(--parameter-columns,1),minmax(0,1fr)); gap:9px 12px; padding:10px 12px; border:1px solid var(--line); border-radius:7px; background:var(--wash) } .parameter-group-title { grid-column:1/-1; color:var(--muted); font-size:11px; font-weight:700; letter-spacing:.04em; text-transform:uppercase } .parameter-control { display:flex; flex-direction:column; gap:3px; min-width:0; color:var(--muted); font-size:11px } .parameter-control input,.parameter-control select { width:100%; min-height:34px; padding:5px 8px; color:var(--ink) } .channel { min-width:0; min-height:0; height:100%; overflow:hidden; border-right:1px solid var(--line); border-bottom:1px solid var(--line); background:white } .channel:nth-child(2n) { border-right:0 } .layout-column { display:flex; flex-direction:column; gap:8px; height:100%; min-height:0; overflow:auto } .layout-column > .plotly-view,.layout-column > .matplotlib-view { flex:1 } .layout-row { display:flex; gap:8px; height:100%; min-height:0 } .layout-panel { height:100%; min-height:0; overflow:auto; padding:12px; border:1px solid var(--line); border-radius:7px } .prose,.text-view { padding:16px; color:var(--ink) } .prose h1,.prose h2,.prose h3 { margin:0 0 8px } .table-wrap { overflow:auto; padding:8px } .data-table { width:100%; border-collapse:collapse; font-size:12px } .data-table th { position:sticky; top:0; background:var(--wash); color:var(--muted); text-align:left } .data-table th,.data-table td { padding:7px 9px; border-bottom:1px solid var(--line); white-space:nowrap } .empty,.error { padding:36px; text-align:center; color:var(--muted); border:1px dashed #bac9cd; border-radius:10px }
    .error { color:#8c2e2e; background:#fff7f7 } @media(max-width:700px){header{padding:0 14px}header span{display:none}main{margin-top:20px}main.item-page{width:calc(100% - 12px);margin-top:6px}.toolbar{flex-wrap:wrap}.playback-grid{grid-template-columns:1fr;grid-template-rows:repeat(var(--grid-items),minmax(0,1fr))}.channel{border-right:0}.card{grid-template-columns:1fr}.card-tags{text-align:left}.data-toolbar{flex-wrap:wrap}.workspace-sidebar{width:calc(100vw - 12px);top:52px}.sidebar-backdrop{inset:52px 0 0}.control-fields{grid-template-columns:1fr}}
    .layout-column > .view-switcher { flex:1 }
    .live-toggle { border:1px solid var(--line); border-radius:6px; padding:5px 9px; background:white; color:var(--muted); font:600 12px inherit; cursor:pointer } .live-toggle.active { border-color:#b42318; color:#b42318; background:#fff1f0 } html[data-theme="dark"] .live-toggle { background:#193741; color:var(--muted) } html[data-theme="dark"] .live-toggle.active { border-color:#ff7b72; color:#ff9b94; background:#4a2020 }
  </style>
</head>
<body><header><button class="home-title" id="app-home">__BROWSER_TITLE__</button><span>__BROWSER_SUBTITLE__</span><span class="header-spacer"></span><select id="theme-toggle" aria-label="Color theme"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select><button class="sidebar-toggle" id="header-log" aria-label="Log current playback position" title="Log current playback position" hidden>Log</button><button class="sidebar-toggle icon-button" id="header-camera" aria-label="Export all plots as PNG files" title="Export all plots as PNG files" hidden><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M8.5 7 10 5h4l1.5 2H19a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h3.5Z"/><circle cx="12" cy="13" r="3.25"/></svg></button><button class="sidebar-toggle" id="header-download" hidden>Download .mat</button><button class="sidebar-toggle" id="header-details" data-sidebar-toggle aria-expanded="false" hidden>Details</button><button class="fullscreen-toggle" id="fullscreen-toggle" aria-label="Enter fullscreen" aria-pressed="false">⛶</button></header><main id="app"><div class="empty">Loading workspaces…</div></main>
<script src="/assets/plotly.min.js"></script>
<script>
const app=document.querySelector('#app');
const appHome=document.querySelector('#app-home');
const headerDetails=document.querySelector('#header-details');
const headerDownload=document.querySelector('#header-download');
const headerCamera=document.querySelector('#header-camera');
const headerLog=document.querySelector('#header-log');
const fullscreenToggle=document.querySelector('#fullscreen-toggle');
const themeToggle=document.querySelector('#theme-toggle');let themePreference=localStorage.getItem('workspace-browser-theme')||'system',activeThemeRefresh=null;
function resolvedTheme(){return themePreference==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):themePreference}function applyTheme(){document.documentElement.dataset.theme=resolvedTheme();themeToggle.value=themePreference}async function refreshTheme(){applyTheme();if(activeThemeRefresh)await activeThemeRefresh()}applyTheme();themeToggle.onchange=async()=>{themePreference=themeToggle.value;localStorage.setItem('workspace-browser-theme',themePreference);try{await refreshTheme()}catch(error){alert(`Theme refresh failed: ${error.message}`)}};matchMedia('(prefers-color-scheme: dark)').addEventListener('change',async()=>{if(themePreference==='system'){try{await refreshTheme()}catch(error){console.error(error)}}});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=async path=>{const r=await fetch(path);if(!r.ok)throw new Error((await r.json()).detail||`Request failed (${r.status})`);return r.json()};
const apiPost=async(path,payload)=>{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(!r.ok)throw new Error((await r.json()).detail||`Request failed (${r.status})`);return r.json()};
const fail=e=>app.innerHTML=`<div class="error"><b>Unable to load this page</b><br>${esc(e.message)}</div>`;
let playbackTimer=null,playbackPosition=0,playbackPaused=false,playbackFollowLive=false,windowStart=0,windowEnd=null,segmentId=null,plotResizeObserver=null,windowOverviewResizeObserver=null,dataStageResizeFrame=null,progressLogs=[],activePlaybackSeek=null;
const viewSelections={};
function markdown(value){return esc(value).replace(/^### (.+)$/gm,'<h3>$1</h3>').replace(/^## (.+)$/gm,'<h2>$1</h2>').replace(/^# (.+)$/gm,'<h1>$1</h1>').replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\n/g,'<br>')}
function plotlyFigure(figure,viewName){const id=`plotly-${encodeURIComponent(viewName)}`;return `<div id="${id}" class="plotly-view" data-plot-view="${esc(viewName)}"></div>`}
function matplotlibFigure(payload,viewName){return `<img class="matplotlib-view" data-matplotlib-view="${esc(viewName)}" alt="${esc(viewName)}" src="data:image/png;base64,${payload}">`}
const plotlyConfig={responsive:true,displaylogo:false};
function setPlotlyRuntime(started){const target=document.querySelector('[data-client-stat="plotly-runtime"]');if(target)target.textContent=`${(performance.now()-started).toFixed(1)} ms`}
async function initializePlotlyViews(views){const started=performance.now(),jobs=[];document.querySelectorAll('[data-plot-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.plotView);if(view&&view.kind==='plotly')jobs.push(Plotly.newPlot(target,view.value.data||[],view.value.layout||{},plotlyConfig))});await Promise.all(jobs);setPlotlyRuntime(started)}
async function updatePlotlyViews(views){const started=performance.now(),jobs=[];document.querySelectorAll('[data-plot-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.plotView);if(view&&view.kind==='plotly')jobs.push(Plotly.react(target,view.value.data||[],view.value.layout||{},plotlyConfig))});await Promise.all(jobs);setPlotlyRuntime(started)}
function updateMatplotlibViews(views){document.querySelectorAll('[data-matplotlib-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.matplotlibView);if(view&&view.kind==='matplotlib')target.src=`data:image/png;base64,${view.value}`})}
function resizePlots(){document.querySelectorAll('[data-plot-view]').forEach(target=>Plotly.Plots.resize(target))}
function sizeDataStage(){const stage=document.querySelector('.data-stage');if(!stage)return;const available=Math.max(280,Math.floor(window.innerHeight-stage.getBoundingClientRect().top-4));stage.style.height=`${available}px`;cancelAnimationFrame(dataStageResizeFrame);dataStageResizeFrame=requestAnimationFrame(resizePlots)}
function observeDataStage(){plotResizeObserver?.disconnect();const stage=document.querySelector('.data-stage');if(!stage)return;plotResizeObserver=new ResizeObserver(()=>{cancelAnimationFrame(dataStageResizeFrame);dataStageResizeFrame=requestAnimationFrame(resizePlots)});plotResizeObserver.observe(stage);window.addEventListener('resize',sizeDataStage,{passive:true});sizeDataStage()}
function stopPlayback(){clearInterval(playbackTimer);playbackTimer=null;activePlaybackSeek=null;plotResizeObserver?.disconnect();plotResizeObserver=null;windowOverviewResizeObserver?.disconnect();windowOverviewResizeObserver=null;cancelAnimationFrame(dataStageResizeFrame);dataStageResizeFrame=null;window.removeEventListener('resize',sizeDataStage)}
function syncFullscreenToggle(){const active=Boolean(document.fullscreenElement);fullscreenToggle.setAttribute('aria-label',active?'Exit fullscreen':'Enter fullscreen');fullscreenToggle.setAttribute('aria-pressed',String(active));fullscreenToggle.textContent=active?'×':'⛶';sizeDataStage()}
fullscreenToggle.onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen()}catch(e){/* Browser fullscreen can be unavailable in embedded contexts. */}};
document.addEventListener('fullscreenchange',syncFullscreenToggle);
function tableRows(value){if(Array.isArray(value))return value;if(!value||typeof value!=='object')return[];const columns=Object.keys(value),indices=[...new Set(columns.flatMap(column=>Object.keys(value[column]||{})))];return indices.map(index=>Object.fromEntries(columns.map(column=>[column,value[column]?.[index]])))}
function tableHtml(value){const rows=tableRows(value);if(!rows.length)return '<div class="empty">No rows</div>';const columns=[...new Set(rows.flatMap(row=>Object.keys(row)))];return `<div class="table-wrap"><table class="data-table"><thead><tr>${columns.map(column=>`<th>${esc(column)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${columns.map(column=>`<td>${esc(statText(row[column]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}
function renderValue(v){if(v.kind==='markdown')return `<article class="prose">${markdown(v.value)}</article>`;if(v.kind==='text')return `<div class="text-view">${esc(v.value)}</div>`;if(v.kind==='table'||v.kind==='dataframe')return tableHtml(v.value);return `<pre>${esc(typeof v.value==='string'?v.value:JSON.stringify(v.value,null,2))}</pre>`}
function renderView(v){if(v.kind==='plotly')return plotlyFigure(v.value,v.name);if(v.kind==='matplotlib')return matplotlibFigure(v.value,v.name);return `<div data-render-view="${esc(v.name)}">${renderValue(v)}</div>`}
function updateGenericViews(views){document.querySelectorAll('[data-render-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.renderView);if(view&&view.kind!=='plotly'&&view.kind!=='matplotlib')target.innerHTML=renderValue(view)})}
function gridTemplate(columns){if(Array.isArray(columns))return columns.map(weight=>`minmax(0,${Number(weight)||1}fr)`).join(' ');const count=Math.max(1,Number(columns)||1);return `repeat(${count},minmax(0,1fr))`}
function renderLayout(node,views,controls,values){if(node.kind==='view_slot'){const view=views.find(v=>v.name===node.view);return view?renderView(view):''}if(node.kind==='control_slot'){const control=controls.find(candidate=>candidate.name===node.props.name);return control?`<label class="parameter-control">${esc(control.label||controlLabel(control.name))}${controlHtml(control,values)}</label>`:''}if(node.kind==='tabs'){const labels=node.children.map((child,i)=>child.props.label||`Tab ${i+1}`);return `<div class="layout-tabs" data-layout-tabs><nav class="tabs">${labels.map((label,i)=>`<button class="tab ${i===0?'active':''}" data-layout-tab="${i}">${esc(label)}</button>`).join('')}</nav><div class="layout-tab-panes">${node.children.map((child,i)=>`<div class="layout-tab-pane ${i===0?'active':''}" data-layout-pane="${i}" aria-hidden="${i!==0}">${renderLayout(child,views,controls,values)}</div>`).join('')}</div></div>`}if(node.kind==='view_switcher'){const key=String(node.props.key),selected=viewSelections[key]||0,labels=node.children.map((child,i)=>child.props.label||`View ${i+1}`),selector=node.props.selector||'buttons',control=selector==='dropdown'?`<select class="view-switcher-select" data-view-select>${labels.map((label,i)=>`<option value="${i}" ${i===selected?'selected':''}>${esc(label)}</option>`).join('')}</select>`:labels.map((label,i)=>`<button class="view-choice ${i===selected?'active':''}" data-view-choice="${i}">${esc(label)}</button>`).join('');return `<div class="view-switcher" data-view-switcher="${esc(key)}"><div class="view-switcher-head"><span class="view-switcher-label">${esc(node.props.label||'View')}</span>${control}</div>${node.children.map((child,i)=>`<div class="view-pane ${i===selected?'active':''}" data-view-pane="${i}" data-view-label="${esc(labels[i])}" aria-hidden="${i!==selected}">${renderLayout(child,views,controls,values)}</div>`).join('')}</div>`}const children=node.children.map(child=>renderLayout(child,views,controls,values)).join('');if(node.kind==='control_group')return `<div class="parameter-group" style="--parameter-columns:${Number(node.props.columns)||1}">${node.props.label?`<div class="parameter-group-title">${esc(node.props.label)}</div>`:''}${children}</div>`;if(node.kind==='grid'){const columnCount=Array.isArray(node.props.columns)?node.props.columns.length:Number(node.props.columns)||1,rowCount=Math.ceil(node.children.length/columnCount);return `<div class="playback-grid ${node.children.length===1?'single-plot':''}" style="--grid-template:${gridTemplate(node.props.columns)};--grid-rows:repeat(${rowCount},minmax(0,1fr));--grid-items:${node.children.length}">${node.children.map(child=>`<div class="channel">${renderLayout(child,views,controls,values)}</div>`).join('')}</div>`}if(node.kind==='column'||node.kind==='stack')return `<div class="layout-column">${children}</div>`;if(node.kind==='row')return `<div class="layout-row">${children}</div>`;if(node.kind==='panel')return `<div class="layout-panel">${children}</div>`;return children}
function bindLayoutTabs(){document.querySelectorAll('[data-layout-tabs]').forEach(root=>{const buttons=root.querySelectorAll(':scope > .tabs > [data-layout-tab]'),panes=root.querySelectorAll(':scope > .layout-tab-panes > [data-layout-pane]');buttons.forEach(button=>button.onclick=()=>{const selected=Number(button.dataset.layoutTab);buttons.forEach((candidate,index)=>candidate.classList.toggle('active',index===selected));panes.forEach((pane,index)=>{pane.classList.toggle('active',index===selected);pane.setAttribute('aria-hidden',String(index!==selected))});requestAnimationFrame(resizePlots)})})}
function bindViewSwitchers(){document.querySelectorAll('.view-switcher[data-view-switcher]').forEach(root=>{const activate=selected=>{viewSelections[root.dataset.viewSwitcher]=selected;root.querySelectorAll('[data-view-choice]').forEach((choice,index)=>choice.classList.toggle('active',index===selected));root.querySelectorAll(':scope > [data-view-pane]').forEach((pane,index)=>{pane.classList.toggle('active',index===selected);pane.setAttribute('aria-hidden',String(index!==selected))});const select=root.querySelector('[data-view-select]');if(select)select.value=selected};root.querySelectorAll('[data-view-choice]').forEach(button=>button.onclick=()=>activate(Number(button.dataset.viewChoice)));const select=root.querySelector('[data-view-select]');if(select)select.onchange=()=>activate(Number(select.value))})}
function renderLogMarkers(config){const target=document.querySelector('#log-markers');if(!target)return;const duration=Math.max(0,Number(config.duration_seconds)||0);target.innerHTML=progressLogs.map(log=>{const position=Math.max(0,Number(log.position_seconds)||0),percent=duration?Math.min(100,position/duration*100):0,label=`Logged at ${position.toFixed(6).replace(/0+$/,'').replace(/\.$/,'')} s · ${log.filename}`;return `<button class="log-marker" type="button" style="left:${percent}%" data-log-position="${position}" aria-label="${esc(label)}" title="${esc(label)}"></button>`}).join('');target.querySelectorAll('[data-log-position]').forEach(marker=>marker.onclick=()=>activePlaybackSeek?.(marker.dataset.logPosition))}
function startFrameworkPlayback(config,refresh){
  const bar=document.querySelector('#playback-bar');if(!bar)return;clearInterval(playbackTimer);if(playbackPosition>config.duration_seconds)playbackPosition=0;
  const slider=bar.querySelector('#position'),current=bar.querySelector('#current-time'),counter=bar.querySelector('#counter'),live=bar.querySelector('#jump-live');let updating=false;
  const updateClock=()=>{slider.max=config.duration_seconds;current.max=config.duration_seconds;slider.value=playbackPosition;current.value=Number(playbackPosition.toFixed(9));counter.textContent=`/ ${config.duration_seconds.toFixed(3)} s`;live?.classList.toggle('active',playbackFollowLive);renderLogMarkers(config)};
  const update=async()=>{if(updating)return;updating=true;try{await refresh()}finally{updating=false}};
  const seek=async value=>{const parsed=Number(value);if(!Number.isFinite(parsed)){updateClock();return}playbackFollowLive=false;playbackPosition=Math.min(config.duration_seconds,Math.max(0,parsed));updateClock();await update()};
  activePlaybackSeek=seek;
  slider.step=config.step_seconds;slider.oninput=e=>seek(e.target.value);
  current.onchange=e=>seek(e.target.value);current.onkeydown=e=>{if(e.key==='Enter')e.currentTarget.blur()};
  if(live)live.onclick=async()=>{playbackFollowLive=true;playbackPaused=false;bar.querySelector('#toggle').textContent='❚❚ Pause';await update();playbackPosition=config.duration_seconds;updateClock()};
  bar.querySelector('#toggle').onclick=()=>{playbackPaused=!playbackPaused;bar.querySelector('#toggle').textContent=playbackPaused?'▶ Play':'❚❚ Pause'};
  updateClock();const interval=config.refresh_interval_seconds??config.step_seconds;playbackTimer=setInterval(async()=>{if(playbackPaused||updating)return;if(playbackFollowLive){await update();playbackPosition=config.duration_seconds;updateClock();return}playbackPosition+=config.step_seconds;if(playbackPosition>config.duration_seconds)playbackPosition=config.loop?0:config.duration_seconds;updateClock();await update()},interval*1000)
}
function startFrameworkWindowed(config,refresh){
  windowOverviewResizeObserver?.disconnect();const root=document.querySelector('#windowed-bar'),track=root?.querySelector('#windowed-track');if(!root||!track)return;const duration=Number(config.duration_seconds)||0,minimum=Math.max(Number(config.minimum_window_seconds)||0,1e-12),step=Number(config.step_seconds)||minimum;
  if(windowEnd==null){windowStart=Number(config.window_start_seconds)||0;windowEnd=Number(config.window_end_seconds)||Math.min(duration,windowStart+minimum)}
  const canvas=track.querySelector('canvas'),selection=track.querySelector('#windowed-selection'),left=track.querySelector('#windowed-left'),right=track.querySelector('#windowed-right'),startInput=root.querySelector('#windowed-start'),endInput=root.querySelector('#windowed-end'),totalLabel=root.querySelector('#windowed-total');let drag=null,updating=false,pending=false,commitTimer=null;
  const clamp=()=>{windowStart=Math.min(duration-minimum,Math.max(0,Number(windowStart)||0));windowEnd=Math.min(duration,Math.max(windowStart+minimum,Number(windowEnd)||minimum))};
  const drawOverview=()=>{const rect=canvas.getBoundingClientRect(),ratio=devicePixelRatio||1,width=Math.max(1,Math.round(rect.width*ratio)),height=Math.max(1,Math.round(rect.height*ratio));if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height}const context=canvas.getContext('2d'),values=(config.overview_values||[]).map(Number).filter(Number.isFinite);context.clearRect(0,0,width,height);if(values.length<2)return;const limits=values.reduce((result,value)=>[Math.min(result[0],value),Math.max(result[1],value)],[Infinity,-Infinity]),low=limits[0],span=limits[1]-low||1,style=getComputedStyle(document.documentElement);context.beginPath();values.forEach((value,index)=>{const x=index/(values.length-1)*width,y=height-2-(value-low)/span*(height-4);if(index)context.lineTo(x,y);else context.moveTo(x,y)});context.strokeStyle=style.getPropertyValue('--accent').trim();context.lineWidth=Math.max(1,ratio);context.stroke()};
  const render=()=>{clamp();const leftPercent=duration?windowStart/duration*100:0,rightPercent=duration?windowEnd/duration*100:100;selection.style.left=`${leftPercent}%`;selection.style.width=`${rightPercent-leftPercent}%`;left.style.left=`${leftPercent}%`;right.style.left=`${rightPercent}%`;startInput.value=Number(windowStart.toFixed(9));endInput.value=Number(windowEnd.toFixed(9));totalLabel.textContent=`/ ${duration.toFixed(3)} s`;left.setAttribute('aria-valuenow',String(windowStart));right.setAttribute('aria-valuenow',String(windowEnd));drawOverview()};
  const commit=async()=>{if(updating){pending=true;return}updating=true;try{await refresh()}finally{updating=false;if(pending){pending=false;void commit()}}};
  const scheduleCommit=()=>{if(commitTimer!==null)return;commitTimer=setTimeout(()=>{commitTimer=null;void commit()},75)};
  const finalCommit=()=>{if(commitTimer!==null){clearTimeout(commitTimer);commitTimer=null}void commit()};
  const begin=(kind,event)=>{event.preventDefault();drag={kind,pointer:event.pointerId,x:event.clientX,start:windowStart,end:windowEnd};track.setPointerCapture(event.pointerId)};
  left.onpointerdown=event=>begin('left',event);right.onpointerdown=event=>begin('right',event);selection.onpointerdown=event=>begin('move',event);
  track.onpointermove=event=>{if(!drag||drag.pointer!==event.pointerId)return;const delta=(event.clientX-drag.x)/Math.max(1,track.clientWidth)*duration;if(drag.kind==='left')windowStart=Math.min(drag.end-minimum,Math.max(0,drag.start+delta));else if(drag.kind==='right')windowEnd=Math.max(drag.start+minimum,Math.min(duration,drag.end+delta));else{const width=drag.end-drag.start;windowStart=Math.min(duration-width,Math.max(0,drag.start+delta));windowEnd=windowStart+width}render();scheduleCommit()};
  track.onpointerup=event=>{if(!drag||drag.pointer!==event.pointerId)return;drag=null;track.releasePointerCapture(event.pointerId);finalCommit()};track.onpointercancel=()=>{drag=null;finalCommit()};
  const editEndpoint=(kind,value)=>{const parsed=Number(value);if(!Number.isFinite(parsed)){render();return}if(kind==='start')windowStart=parsed;else windowEnd=parsed;render();finalCommit()};
  startInput.onchange=event=>editEndpoint('start',event.target.value);endInput.onchange=event=>editEndpoint('end',event.target.value);startInput.onkeydown=endInput.onkeydown=event=>{if(event.key==='Enter')event.currentTarget.blur()};
  const keyboard=(kind,event)=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const delta=event.key==='ArrowLeft'?-step:step;if(kind==='left')windowStart+=delta;else if(kind==='right')windowEnd+=delta;else{const width=windowEnd-windowStart;windowStart=Math.min(duration-width,Math.max(0,windowStart+delta));windowEnd=windowStart+width}render();void commit()};
  left.onkeydown=event=>keyboard('left',event);right.onkeydown=event=>keyboard('right',event);selection.onkeydown=event=>keyboard('move',event);windowOverviewResizeObserver=new ResizeObserver(render);windowOverviewResizeObserver.observe(track);render()
}
function startFrameworkSegmented(config,refresh){
  const root=document.querySelector('#segmented-bar'),track=root?.querySelector('#segmented-track'),previous=root?.querySelector('#segment-previous'),next=root?.querySelector('#segment-next'),counter=root?.querySelector('#segment-count'),time=root?.querySelector('#segment-time');if(!root||!track)return;let updating=false;
  const available=()=>config.segments||[];
  const selectedIndex=()=>Math.max(0,available().findIndex(segment=>segment.identifier===segmentId));
  const render=()=>{const segments=available();if(!segments.length)return;if(!segments.some(segment=>segment.identifier===segmentId))segmentId=config.selected_segment_id||segments[0].identifier;const index=selectedIndex(),selected=segments[index],duration=Number(config.duration_seconds)||0;track.innerHTML=segments.map(segment=>{const percent=duration?Math.min(100,Math.max(0,Number(segment.start_seconds)/duration*100)):0,label=segment.label||segment.identifier,title=`${label} · ${Number(segment.start_seconds).toFixed(3)} s · ${Number(segment.duration_seconds).toFixed(3)} s`;return `<button class="segment-marker ${segment.identifier===segmentId?'active':''}" type="button" style="left:${percent}%" data-segment-id="${esc(segment.identifier)}" aria-label="${esc(title)}" title="${esc(title)}"></button>`}).join('');counter.textContent=`${index+1} / ${segments.length}`;time.textContent=`${Number(selected.start_seconds).toFixed(3)}–${(Number(selected.start_seconds)+Number(selected.duration_seconds)).toFixed(3)} / ${duration.toFixed(3)} s`;previous.disabled=index===0;next.disabled=index===segments.length-1;track.querySelectorAll('[data-segment-id]').forEach(marker=>marker.onclick=()=>select(marker.dataset.segmentId))};
  const select=async identifier=>{if(updating||identifier===segmentId)return;segmentId=identifier;render();updating=true;try{await refresh()}finally{updating=false;render()}};
  previous.onclick=()=>{const segments=available(),index=selectedIndex();if(index>0)void select(segments[index-1].identifier)};next.onclick=()=>{const segments=available(),index=selectedIndex();if(index<segments.length-1)void select(segments[index+1].identifier)};render()
}
function startFrameworkRefresh(config,refresh){let updating=false;playbackTimer=setInterval(async()=>{if(updating)return;updating=true;try{await refresh()}finally{updating=false}},config.interval_seconds*1000)}
const controlLabel=name=>name.split('_').map(x=>x[0].toUpperCase()+x.slice(1)).join(' ');
function controlHtml(control,values){const value=values[control.name]??control.default;if(control.control_type==='select')return `<select data-control="${esc(control.name)}">${control.options.map(option=>`<option value="${esc(option)}" ${String(value)===String(option)?'selected':''}>${esc(option)}</option>`).join('')}</select>`;if(control.control_type==='color')return `<input type="color" data-control="${esc(control.name)}" value="${esc(value)}">`;if(control.control_type==='integer'||control.control_type==='float')return `<input type="number" data-control="${esc(control.name)}" value="${esc(value)}" ${control.minimum==null?'':`min="${esc(control.minimum)}"`} ${control.maximum==null?'':`max="${esc(control.maximum)}"`} ${control.step==null?'':`step="${esc(control.step)}"`}>`;return `<input data-control="${esc(control.name)}" value="${esc(value)}">`}
function controlFieldHtml(control,values){return `<label>${esc(control.label||controlLabel(control.name))}${controlHtml(control,values)}</label>`}
function stylePickerHtml(controls,values){const color=controls.find(control=>control.control_type==='color'),value=color?(values[color.name]??color.default):'#60717d',label=controls.find(control=>control.picker_label)?.picker_label||controlLabel(controls[0].picker);return `<details class="style-picker" data-style-picker="${esc(controls[0].picker)}"><summary><span class="style-swatch" data-style-swatch style="background:${esc(value)}"></span><span class="style-picker-name">${esc(label)}</span></summary><div class="style-picker-fields">${controls.map(control=>controlFieldHtml(control,values)).join('')}</div></details>`}
function controlGroupHtml(controls,values){const regular=controls.filter(control=>!control.picker),pickers=controls.filter(control=>control.picker).reduce((result,control)=>{(result[control.picker]??=[]).push(control);return result},{});return `<div class="control-fields">${regular.map(control=>controlFieldHtml(control,values)).join('')}</div>${Object.keys(pickers).length?`<div class="style-picker-list">${Object.values(pickers).map(picker=>stylePickerHtml(picker,values)).join('')}</div>`:''}`}
const statText=value=>value!=null&&typeof value==='object'?JSON.stringify(value):String(value??'—');
function statisticsRows(statistics){return Object.entries(statistics||{}).map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(statText(value))}</dd></div>`).join('')}
function sidebarHtml(workspaceName,page){const details=page.controls.filter(control=>control.placement!=='inline'),groups=details.reduce((result,control)=>{const label=control.group||'Analysis settings';(result[label]??=[]).push(control);return result},{}),settings=Object.entries(groups).map(([label,controls])=>`<section><h2>${esc(label)}</h2>${controlGroupHtml(controls,page.control_values)}</section>`).join('');return `<button class="sidebar-backdrop" data-sidebar-backdrop aria-label="Close details"></button><aside class="workspace-sidebar" data-workspace-sidebar aria-label="Workspace details"><div class="sidebar-head"><div class="sidebar-title"><div class="crumb"><button id="home">Workspaces</button> / <button id="back">${esc(workspaceName)}</button></div><h1>${esc(page.title)}</h1><span class="subtitle">${esc(page.subtitle||'')}</span></div><button class="sidebar-close" data-sidebar-close aria-label="Close details">Close</button></div><div class="analysis-panel">${settings}<section><h2>View details</h2><dl class="view-stats" id="view-stats">${statisticsRows(page.statistics)}<div><dt>Plotly render</dt><dd data-client-stat="plotly-runtime">—</dd></div></dl></section></div></aside>`}
function updateStatistics(statistics){const target=document.querySelector('#view-stats');if(target)target.innerHTML=`${statisticsRows(statistics)}<div><dt>Plotly render</dt><dd data-client-stat="plotly-runtime">—</dd></div>`}
function bindSidebar(){const sidebar=document.querySelector('[data-workspace-sidebar]'),backdrop=document.querySelector('[data-sidebar-backdrop]'),toggle=document.querySelector('[data-sidebar-toggle]');if(!sidebar||!backdrop||!toggle)return;const setOpen=open=>{sidebar.classList.toggle('open',open);backdrop.classList.toggle('open',open);toggle.setAttribute('aria-expanded',String(open))};toggle.onclick=()=>setOpen(!sidebar.classList.contains('open'));backdrop.onclick=()=>setOpen(false);sidebar.querySelector('[data-sidebar-close]').onclick=()=>setOpen(false)}
async function catalog(navigate=true){stopPlayback();activeThemeRefresh=null;headerDetails.hidden=true;headerDownload.hidden=true;headerCamera.hidden=true;headerLog.hidden=true;app.className='';if(navigate)history.pushState(null,'','/');try{const {workspaces}=await api('/workspaces');app.innerHTML=`<h1>Workspaces</h1><p class="lead">Choose a workspace to discover its available items.</p><div class="list">${workspaces.map(w=>`<article class="card" data-id="${esc(w.id)}"><div><span class="tag">${esc(w.category||'workspace')}</span><h2>${esc(w.name)}</h2></div><p class="muted">${esc(w.description)}</p><div class="card-tags">${w.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div></article>`).join('')}</div>`;document.querySelectorAll('.card').forEach(x=>x.onclick=()=>items(x.dataset.id,workspaces.find(w=>w.id===x.dataset.id).name))}catch(e){fail(e)}}
async function items(id,name,navigate=true){stopPlayback();activeThemeRefresh=null;headerDetails.hidden=true;headerDownload.hidden=true;headerCamera.hidden=true;headerLog.hidden=true;app.className='';if(navigate)history.pushState(null,'',`/workspace/${encodeURIComponent(id)}`);app.innerHTML='<div class="empty">Discovering items…</div>';try{const {items:list}=await api(`/workspaces/${encodeURIComponent(id)}/items`);app.innerHTML=`<div class="crumb"><button id="home">Workspaces</button> / ${esc(name)}</div><h1>${esc(name)}</h1><p class="lead">Browse and open discovered items.</p><div class="toolbar"><input id="search" type="search" placeholder="Search items…"><button class="primary" id="refresh">Refresh list</button></div><div class="list" id="items"></div>`;const draw=()=>{const q=document.querySelector('#search').value.toLowerCase();const shown=list.filter(x=>!q||`${x.title} ${x.subtitle||''} ${x.tags.join(' ')}`.toLowerCase().includes(q));document.querySelector('#items').innerHTML=shown.length?shown.map(x=>`<article class="card" data-item="${esc(x.id)}"><div><h2>${esc(x.title)}</h2></div><p class="muted">${esc(x.subtitle||'')}</p><div class="card-tags">${x.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div></article>`).join(''):'<div class="empty">No matching items.</div>';document.querySelectorAll('[data-item]').forEach(x=>x.onclick=()=>openItem(id,name,x.dataset.item))};draw();document.querySelector('#home').onclick=()=>catalog();document.querySelector('#search').oninput=draw;document.querySelector('#refresh').onclick=()=>items(id,name,false)}catch(e){fail(e)}}
async function openItem(wid,wname,iid,navigate=true,controlValues={},preservePlayback=false){
  stopPlayback();activeThemeRefresh=null;headerDetails.hidden=true;headerDownload.hidden=true;headerCamera.hidden=true;headerLog.hidden=true;app.className='item-page';if(!preservePlayback){playbackPosition=0;playbackPaused=false;playbackFollowLive=false;windowStart=0;windowEnd=null;segmentId=null;Object.keys(viewSelections).forEach(key=>delete viewSelections[key])}if(navigate)history.pushState(null,'',`/workspace/${encodeURIComponent(wid)}/item/${encodeURIComponent(iid)}`);app.innerHTML='<div class="empty">Opening item…</div>';
  try{const request=async values=>api(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}?${new URLSearchParams(values)}`),windowValues=()=>windowEnd==null?{}:{__window_start_seconds:windowStart,__window_end_seconds:windowEnd},segmentValues=()=>segmentId==null?{}:{__segment_id:segmentId};let data=await request({...controlValues,...windowValues(),...segmentValues(),__theme:resolvedTheme(),__playback_time_seconds:playbackPosition}),p=data.page,requestGeneration=0;const isPlayback=['seek','live'].includes(p.playback.mode),isWindowed=p.playback.mode==='windowed',isSegmented=p.playback.mode==='segmented';progressLogs=p.logging?.entries||[];
    const playbackConfig=p.playback;if(isWindowed&&windowEnd==null){windowStart=Number(playbackConfig.window_start_seconds)||0;windowEnd=Number(playbackConfig.window_end_seconds)||playbackConfig.duration_seconds}if(isSegmented)segmentId=playbackConfig.selected_segment_id;headerDetails.hidden=false;headerDownload.hidden=false;headerCamera.hidden=false;headerLog.hidden=!p.logging?.enabled;const playbackToolbar=isPlayback?`<div class="data-toolbar"><div class="playback-bar" id="playback-bar"><button class="primary" id="toggle">${playbackPaused?'▶ Play':'❚❚ Pause'}</button><div class="playback-track"><input id="position" aria-label="Playback position" type="range" min="0" value="${playbackPosition}"><div class="log-markers" id="log-markers" aria-label="Saved progress logs"></div></div><input id="current-time" aria-label="Current playback time in seconds" type="number" min="0" step="any" value="${playbackPosition}"><span id="counter"></span>${playbackConfig.mode==='live'?'<button class="live-toggle" id="jump-live">Live</button>':''}</div></div>`:isWindowed?`<div class="data-toolbar"><div class="windowed-bar" id="windowed-bar">${(playbackConfig.overview_values||[]).length?`<span class="windowed-label" title="${esc(playbackConfig.overview_label||'Signal overview')}">${esc(playbackConfig.overview_label||'Signal overview')}</span>`:''}<input class="windowed-time" id="windowed-start" aria-label="Window start time in seconds" type="number" min="0" max="${playbackConfig.duration_seconds}" step="any"><div class="windowed-track" id="windowed-track"><canvas class="windowed-overview" aria-hidden="true"></canvas><button class="windowed-selection" id="windowed-selection" type="button" aria-label="Move selected window"></button><button class="windowed-handle" id="windowed-left" type="button" role="slider" aria-label="Window start" aria-valuemin="0" aria-valuemax="${playbackConfig.duration_seconds}"></button><button class="windowed-handle" id="windowed-right" type="button" role="slider" aria-label="Window end" aria-valuemin="0" aria-valuemax="${playbackConfig.duration_seconds}"></button></div><input class="windowed-time" id="windowed-end" aria-label="Window stop time in seconds" type="number" min="0" max="${playbackConfig.duration_seconds}" step="any"><span class="windowed-total" id="windowed-total"></span></div></div>`:isSegmented?`<div class="data-toolbar"><div class="segmented-bar" id="segmented-bar"><button id="segment-previous" type="button">Previous</button><div class="segmented-track" id="segmented-track" aria-label="Available result segments"></div><span class="segment-count" id="segment-count"></span><span class="segment-time" id="segment-time"></span><button id="segment-next" type="button">Next</button></div></div>`:'';app.innerHTML=`${playbackToolbar}${sidebarHtml(wname,p)}<section class="data-stage"><div id="active-view" class="view"></div></section>`;
    document.querySelector('#active-view').innerHTML=renderLayout(p.layout,p.rendered_views,p.controls,p.control_values);bindLayoutTabs();bindViewSwitchers();bindSidebar();observeDataStage();void initializePlotlyViews(p.rendered_views);requestAnimationFrame(resizePlots);
    const selected=()=>({...Object.fromEntries([...document.querySelectorAll('[data-control]')].map(c=>[c.dataset.control,c.value])),...windowValues(),...segmentValues(),__theme:resolvedTheme(),__playback_follow_live:playbackFollowLive});
    const currentViewContext=()=>({active_tab:document.querySelector('.tab.active')?.textContent?.trim()||null,view_selections:Object.fromEntries([...document.querySelectorAll('[data-view-switcher]')].map(root=>[root.dataset.viewSwitcher,root.querySelector(':scope > .view-pane.active')?.dataset.viewLabel||null]))});
    const writeLog=async()=>{const original=headerLog.textContent;headerLog.disabled=true;headerLog.textContent='Saving…';try{const result=await apiPost(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}/logs`,{control_values:{...selected(),__playback_time_seconds:playbackPosition},...currentViewContext()});progressLogs.push(result);renderLogMarkers(playbackConfig);headerLog.textContent='Saved';headerLog.title=`Saved ${result.filename}`;setTimeout(()=>{headerLog.textContent=original;headerLog.title='Log current playback position'},1200)}catch(error){headerLog.textContent=original;alert(`Log failed: ${error.message}`)}finally{headerLog.disabled=false}};
    const runExport=async(format,button)=>{const original=button.innerHTML,originalLabel=button.getAttribute('aria-label'),originalTitle=button.title;button.disabled=true;if(format==='mat')button.textContent='Preparing .mat…';else{button.setAttribute('aria-label','Rendering plots');button.title='Rendering plots'}try{const query=new URLSearchParams({...selected(),__playback_time_seconds:playbackPosition,__include_static_views:true,format}),job=await api(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}/exports?${query}`);let status;do{await new Promise(resolve=>setTimeout(resolve,350));status=await api(job.status_url)}while(status.status==='pending'||status.status==='running');if(status.status==='error')throw new Error(status.detail);for(const file of status.files){const link=document.createElement('a');link.href=file.url;link.download=file.name;link.click()}}catch(error){alert(`Export failed: ${error.message}`)}finally{button.disabled=false;button.innerHTML=original;if(originalLabel==null)button.removeAttribute('aria-label');else button.setAttribute('aria-label',originalLabel);button.title=originalTitle}};
    headerLog.onclick=writeLog;headerDownload.onclick=()=>runExport('mat',headerDownload);headerCamera.onclick=()=>runExport('png',headerCamera);
    const refresh=async(includeStatic=false)=>{const generation=++requestGeneration,result=await request({...selected(),__playback_time_seconds:playbackPosition,__include_static_views:includeStatic});if(generation!==requestGeneration)return false;data=result;Object.assign(playbackConfig,result.page.playback);p=result.page;p.playback=playbackConfig;if(playbackFollowLive)playbackPosition=playbackConfig.duration_seconds;updateStatistics(p.statistics);await updatePlotlyViews(p.rendered_views);updateMatplotlibViews(p.rendered_views);updateGenericViews(p.rendered_views);return true};activeThemeRefresh=async()=>{const applied=await refresh(true);if(isWindowed&&applied)startFrameworkWindowed(p.playback,refresh);else if(isSegmented&&applied)startFrameworkSegmented(p.playback,refresh)};
    const settingsChanged=async()=>{if(isPlayback)clearInterval(playbackTimer);const applied=await refresh(true);if(isPlayback&&applied)startFrameworkPlayback(p.playback,refresh);else if(isWindowed&&applied)startFrameworkWindowed(p.playback,refresh);else if(isSegmented&&applied)startFrameworkSegmented(p.playback,refresh)};
    if(isPlayback)startFrameworkPlayback(p.playback,refresh);else if(isWindowed)startFrameworkWindowed(p.playback,refresh);else if(isSegmented)startFrameworkSegmented(p.playback,refresh);else if(p.refresh.enabled)startFrameworkRefresh(p.refresh,refresh);document.querySelectorAll('[data-control]').forEach(x=>{x.onchange=settingsChanged;if(x.type==='color')x.oninput=()=>{const swatch=x.closest('[data-style-picker]')?.querySelector('[data-style-swatch]');if(swatch)swatch.style.background=x.value}});document.querySelector('#home').onclick=()=>catalog();document.querySelector('#back').onclick=()=>items(wid,wname)
  }catch(e){fail(e)}}
async function boot(){const parts=location.pathname.split('/').filter(Boolean).map(decodeURIComponent);if(parts[0]!=='workspace')return catalog(false);try{const {workspaces}=await api('/workspaces'),workspace=workspaces.find(w=>w.id===parts[1]);if(!workspace)return catalog(false);if(parts[2]==='item'&&parts[3])return openItem(workspace.id,workspace.name,parts[3],false);return items(workspace.id,workspace.name,false)}catch(e){fail(e)}}
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


@dataclass
class WorkspaceBrowserApp:
    title: str = "Scientific Workspace Browser"
    subtitle: str = "Explore scientific and analytical results"
    registry: WorkspaceRegistry | None = None
    reload_workspaces: bool = False
    workspace_modules: tuple[WorkspaceModuleRegistration, ...] = ()
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

        return [
            {
                "id": item.identifier,
                "title": item.title,
                "subtitle": item.subtitle,
                "source_reference": item.source_reference,
                "timestamp": item.timestamp.isoformat() if item.timestamp else None,
                "tags": list(item.tags),
                "summary_fields": item.summary_fields,
            }
            for item in paged
        ]

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
        statistics["View callbacks"] = f"{(time.perf_counter() - callbacks_started) * 1_000:.1f} ms"
        log_directory = _item_log_directory(opened.item)
        logging_enabled = opened.page.playback.mode in {"seek", "live"} and log_directory is not None
        return {
            "item": {
                "id": opened.item.identifier,
                "title": opened.item.title,
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
                "logging": {
                    "enabled": logging_enabled,
                    "entries": _item_log_entries(item_id, log_directory) if logging_enabled else [],
                },
                "views": [view.name for view in opened.page.views],
                "rendered_views": rendered_views,
                "layout": _layout_to_dict(opened.page.layout),
                "metadata": opened.page.metadata,
                "actions": list(opened.page.actions),
            },
        }

    def write_item_log(
        self,
        workspace_id: str,
        item_id: str,
        control_values: dict[str, object] | None,
        context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Persist a human-readable progress note beside a local seek/live data source."""
        workspace = self.registry.get(workspace_id)
        requested_values = dict(control_values or {})
        open_with_values = getattr(workspace, "open_item_with_values", None)
        opened = open_with_values(item_id, requested_values) if callable(open_with_values) else workspace.open_item(item_id)
        opened.page.validate()
        if opened.page.playback.mode not in {"seek", "live"}:
            raise ValueError("Progress logs are available only for seek or live playback")
        log_directory = _item_log_directory(opened.item)
        if log_directory is None:
            raise ValueError("Progress logs require a local file-backed data source")

        values = {control.name: control.default for control in opened.page.controls}
        values.update(requested_values)
        logged_at = datetime.now(timezone.utc)
        position = _seconds_token(values.get("__playback_time_seconds", 0.0))
        filename = (
            f"{_filename_component(item_id)}-t{position}s-"
            f"{logged_at.strftime('%Y%m%dT%H%M%S.%fZ')}.txt"
        )
        log_directory.mkdir(parents=True, exist_ok=True)
        target = log_directory / filename
        source_reference = opened.item.source_reference or ""
        user_parameters = {
            control.name: values.get(control.name, control.default)
            for control in opened.page.controls
        }
        note_context = dict(context or {})
        note = "\n".join(
            (
                "Scientific Workspace Browser progress log",
                "",
                f"Logged at (UTC): {logged_at.isoformat()}",
                f"Workspace: {workspace.metadata.display_name} ({workspace_id})",
                f"Item: {opened.item.title} ({item_id})",
                f"Source: {source_reference}",
                f"Playback mode: {opened.page.playback.mode}",
                f"Playback position: {position} s",
                f"Playback duration: {opened.page.playback.duration_seconds:g} s",
                f"Following live: {str(values.get('__playback_follow_live', False)).lower()}",
                f"Active tab: {note_context.get('active_tab') or 'unknown'}",
                "",
                "View selections:",
                json.dumps(note_context.get("view_selections", {}), indent=2, sort_keys=True, default=str),
                "",
                "Parameters:",
                json.dumps(user_parameters, indent=2, sort_keys=True, default=str),
                "",
                "Page metadata:",
                json.dumps(opened.page.metadata, indent=2, sort_keys=True, default=str),
                "",
                "Statistics:",
                json.dumps(opened.page.statistics, indent=2, sort_keys=True, default=str),
                "",
            )
        )
        target.write_text(note, encoding="utf-8")
        return {"filename": filename, "logged_at": logged_at.isoformat(), "position_seconds": float(position)}

    def write_item_mat(
        self,
        workspace_id: str,
        item_id: str,
        control_values: dict[str, object] | None,
        stream: Any,
    ) -> str:
        """Write the current delivered data and native view data to a MAT file."""
        workspace = self.registry.get(workspace_id)
        requested_values = dict(control_values or {})
        open_with_values = getattr(workspace, "open_item_with_values", None)
        opened = open_with_values(item_id, requested_values) if callable(open_with_values) else workspace.open_item(item_id)
        opened.page.validate()
        values = {control.name: control.default for control in opened.page.controls}
        values.update(requested_values)
        delivered = opened.page.export_callback(requested_values) if opened.page.export_callback is not None else None
        views = [(view.name, view.callback(requested_values)) for view in opened.page.views]
        write_mat_export(
            stream,
            workspace_id=workspace_id,
            item_id=item_id,
            playback=opened.page.playback,
            refresh=opened.page.refresh,
            parameters=values,
            controls=opened.page.controls,
            delivered_data=delivered,
            layout=opened.page.layout,
            metadata=opened.page.metadata,
            statistics=opened.page.statistics,
            views=views,
        )
        return f"{_export_stem(item_id, opened.page.playback.mode, values, delivered)}-analysis.mat"

    def write_item_png_bundle(
        self,
        workspace_id: str,
        item_id: str,
        control_values: dict[str, object] | None,
        stream: Any,
    ) -> tuple[int, str]:
        """Render every plot view for the current state into one ZIP."""
        workspace = self.registry.get(workspace_id)
        requested_values = dict(control_values or {})
        open_with_values = getattr(workspace, "open_item_with_values", None)
        opened = open_with_values(item_id, requested_values) if callable(open_with_values) else workspace.open_item(item_id)
        opened.page.validate()
        values = {control.name: control.default for control in opened.page.controls}
        values.update(requested_values)
        delivered = opened.page.export_callback(requested_values) if opened.page.export_callback is not None else None
        views = [(view.name, view.callback(requested_values)) for view in opened.page.views]
        stem = _export_stem(item_id, opened.page.playback.mode, values, delivered)
        return write_png_bundle(stream, views, filename_prefix=stem), f"{stem}-plots.zip"

    def start_export(
        self,
        workspace_id: str,
        item_id: str,
        control_values: dict[str, object],
        export_format: str,
    ) -> str:
        """Run a MAT or PNG-bundle export on the dedicated export executor."""
        if export_format not in {"mat", "png"}:
            raise ValueError("Export format must be 'mat' or 'png'")
        job_id = uuid4().hex
        directory = Path(mkdtemp(prefix=f"workspace-export-{job_id[:8]}-"))

        def build() -> dict[str, object]:
            if export_format == "mat":
                target = directory / "export.mat"
                with target.open("w+b") as stream:
                    filename = self.write_item_mat(workspace_id, item_id, control_values, stream)
                renamed = directory / filename
                target.replace(renamed)
                return {"format": "mat", "files": [filename]}
            temporary = directory / "plots.zip"
            with temporary.open("w+b") as stream:
                count, filename = self.write_item_png_bundle(workspace_id, item_id, control_values, stream)
            temporary.replace(directory / filename)
            return {"format": "png", "files": [filename], "plot_count": count}

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


def _export_stem(item_id: str, mode: str, values: dict[str, object], delivered: object) -> str:
    item = _filename_component(item_id)
    if mode == "static":
        return f"{item}-full-static"

    sample_rate = _export_member(delivered, "sample_rate")
    start_sample = _export_member(delivered, "start_sample")
    start_seconds: object = values.get(
        "__window_start_seconds" if mode == "windowed" else "__playback_time_seconds",
        0.0,
    )
    if mode == "segmented":
        start_seconds = _export_member(delivered, "start_seconds") or 0.0
    if sample_rate is not None and start_sample is not None:
        try:
            start_seconds = float(start_sample) / float(sample_rate)
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    duration_seconds: object | None = values.get("buffer_seconds")
    if mode == "segmented":
        duration_seconds = _export_member(delivered, "duration_seconds")
    if mode == "windowed" and "__window_end_seconds" in values:
        try:
            duration_seconds = float(values["__window_end_seconds"]) - float(start_seconds)
        except (TypeError, ValueError):
            pass
    samples = _export_member(delivered, "ota_counts")
    if samples is None:
        samples = _export_member(delivered, "samples")
    if sample_rate is not None and hasattr(samples, "shape") and samples.shape:
        try:
            duration_seconds = float(samples.shape[-1]) / float(sample_rate)
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    parts = [item, f"t{_seconds_token(start_seconds)}s"]
    if duration_seconds is not None:
        parts.append(f"buffer{_seconds_token(duration_seconds)}s")
    parts.append(mode)
    return "-".join(parts)


def _item_log_directory(item: Any) -> Path | None:
    """Return a logs directory beside a local item source without creating it."""
    reference = getattr(item, "source_reference", None)
    if not reference:
        return None
    try:
        source = Path(str(reference)).expanduser()
        if not source.is_absolute():
            source = source.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not source.exists():
        return None
    return (source if source.is_dir() else source.parent) / "logs"


def _item_log_entries(item_id: str, directory: Path | None) -> list[dict[str, object]]:
    """Read timeline positions from progress-log filenames without opening every note."""
    if directory is None or not directory.is_dir():
        return []
    prefix = f"{_filename_component(item_id)}-t"
    entries = []
    for path in directory.glob(f"{prefix}*s-*.txt"):
        position_token, separator, _ = path.name[len(prefix) :].partition("s-")
        if not separator:
            continue
        try:
            position = float(position_token)
        except ValueError:
            continue
        entries.append({"filename": path.name, "position_seconds": position})
    return sorted(entries, key=lambda entry: (entry["position_seconds"], entry["filename"]))


def _export_member(value: object, name: str) -> object | None:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _seconds_token(value: object) -> str:
    try:
        return f"{float(value):.9f}".rstrip("0").rstrip(".") or "0"
    except (TypeError, ValueError):
        return "0"


def _filename_component(value: str) -> str:
    component = "".join(character if character.isalnum() or character in "-_." else "_" for character in value)
    return component.strip("-_.") or "workspace"


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
    title: str = "Scientific Workspace Browser",
    *,
    subtitle: str = "Explore scientific and analytical results",
    reload_workspaces: bool = True,
    config_path: str | Path | None = None,
) -> WorkspaceBrowserApp:
    if config_path is not None:
        profile = load_browser_profile(config_path)
        return WorkspaceBrowserApp(
            title=profile.title or title,
            subtitle=profile.subtitle or subtitle,
            reload_workspaces=reload_workspaces,
            workspace_modules=tuple(_profile_registration(spec) for spec in profile.workspaces),
        )
    return WorkspaceBrowserApp(
        title=title,
        subtitle=subtitle,
        reload_workspaces=reload_workspaces,
        workspace_modules=(),
    )


def _make_handler(app: WorkspaceBrowserApp) -> type[BaseHTTPRequestHandler]:
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
            content_type = "application/x-matlab-data" if path.suffix == ".mat" else "application/zip"
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
                    self._write_json(200, {"workspaces": app.list_workspaces()})
                except Exception as exc:
                    self._write_json(500, {"error": "workspace_reload_failed", "detail": str(exc)})
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
                    self._write_json(200, {"items": app.list_items(parts[1], query)})
                    return
                if len(parts) == 4 and parts[0] == "workspaces" and parts[2] == "items":
                    query = {name: values[-1] for name, values in parse_qs(parsed.query).items()}
                    self._write_json(200, app.open_item(parts[1], parts[3], query))
                    return
                if len(parts) == 5 and parts[0] == "workspaces" and parts[2] == "items" and parts[4] == "exports":
                    query = {name: values[-1] for name, values in parse_qs(parsed.query).items()}
                    export_format = str(query.pop("format", "mat"))
                    job_id = app.start_export(parts[1], parts[3], query, export_format)
                    self._write_json(202, {"id": job_id, "status": "pending", "status_url": f"/exports/{job_id}"})
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
                if len(parts) == 5 and parts[0] == "workspaces" and parts[2] == "items" and parts[4] == "logs":
                    payload = self._read_json()
                    control_values = payload.pop("control_values", {})
                    if not isinstance(control_values, dict):
                        raise ValueError("control_values must be an object")
                    self._write_json(201, app.write_item_log(parts[1], parts[3], control_values, payload))
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
    parser = argparse.ArgumentParser(description="Run the Scientific Workspace Browser server")
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
