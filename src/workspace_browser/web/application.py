from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
import time
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


_PLOTLY_JS = get_plotlyjs()


_INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Scientific Workspace Browser</title>
  <style>
    :root { color-scheme: light; --ink:#13212b; --muted:#60717d; --line:#dce5e8; --accent:#087e8b; --wash:#f3f7f7; }
    * { box-sizing:border-box } body { margin:0; font:15px/1.5 system-ui,-apple-system,sans-serif; color:var(--ink); background:#fbfcfc }
    header { height:52px; display:flex; align-items:center; gap:14px; padding:0 22px; color:white; background:#102f3a; box-shadow:0 1px 6px #102f3a2b }
    header b { font-size:16px } header span { color:#b9d0d5; font-size:13px }
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
    .item-header { display:grid; grid-template-columns:1fr; align-items:end; gap:8px; padding:0 4px 10px; border-bottom:1px solid var(--line) } .item-header .crumb { margin:0; font-size:12px } .item-title { min-width:0 } .item-title h1 { display:inline; font-size:20px; margin:0 } .item-title .subtitle { margin-left:10px; color:var(--muted); font-size:13px }
    .data-toolbar { position:sticky; top:0; z-index:20; display:flex; align-items:center; gap:10px; min-height:46px; margin:0 -12px 4px; padding:6px 16px; background:#fbfcfcf2; border-bottom:1px solid var(--line); backdrop-filter:blur(8px) }
    .playback-bar { display:flex; align-items:center; gap:10px; flex:1; min-width:240px } .playback-bar .primary { padding:6px 10px; min-width:72px } .playback-bar input { flex:1; min-height:0; padding:0 } .playback-bar #counter { width:112px; text-align:right; color:var(--muted); font:12px ui-monospace,monospace }
    .control-drawer { position:relative } .control-drawer summary { cursor:pointer; list-style:none; border:1px solid var(--line); border-radius:6px; padding:5px 10px; background:white; color:var(--muted); font-size:12px } .control-drawer summary::-webkit-details-marker { display:none } .analysis-panel { position:absolute; right:0; top:38px; z-index:30; display:flex; flex-direction:column; gap:12px; min-width:340px; max-width:min(680px,calc(100vw - 24px)); padding:12px; border:1px solid var(--line); border-radius:8px; background:white; box-shadow:0 10px 30px #17323c26 } .control-fields { display:flex; align-items:end; flex-wrap:wrap; gap:10px; padding-bottom:12px; border-bottom:1px solid var(--line) } .control-fields:empty { display:none } .control-fields label { display:flex; flex-direction:column; gap:3px; color:var(--muted); font-size:11px } .control-fields select,.control-fields input { min-height:32px; padding:4px 8px; color:var(--ink) } .control-fields select { padding-right:26px } .control-fields input[type=number] { width:110px } .view-stats { display:grid; grid-template-columns:minmax(120px,1fr) auto; gap:6px 18px; margin:0; font-size:12px } .view-stats div { display:contents } .view-stats dt { color:var(--muted) } .view-stats dd { margin:0; text-align:right; color:var(--ink); font:12px ui-monospace,monospace; white-space:nowrap }
    .data-stage { height:400px; min-height:0; overflow:hidden } #active-view,.view { height:100%; min-height:0 } .layout-tabs { position:relative; display:flex; flex-direction:column; height:100%; min-height:0 } .layout-tab-panes { position:relative; flex:1; min-height:0 } .tabs { flex:none; display:flex; gap:4px; overflow-x:auto; border-bottom:1px solid var(--line); margin:0 0 4px; padding-left:4px } .tab { flex:none; border:0; border-bottom:3px solid transparent; background:none; padding:9px 13px 7px; color:var(--muted); font:600 13px inherit; cursor:pointer } .tab.active { color:var(--accent); border-color:var(--accent) } .layout-tab-pane { width:100%; height:100%; min-height:0 } .layout-tab-pane:not(.active) { position:absolute; inset:0; visibility:hidden; pointer-events:none }
    .view h1 { font-size:20px } .view h2 { font-size:16px } .plotly-view { width:100%; height:100%; min-height:0 } .matplotlib-view { display:block; width:100%; height:100%; min-height:0; object-fit:contain; background:white } .playback-grid { display:grid; width:100%; height:100%; min-height:0; grid-template-columns:var(--grid-template,repeat(2,minmax(0,1fr))); grid-template-rows:var(--grid-rows,minmax(0,1fr)); gap:4px } .playback-grid.single-plot { display:block; height:100% } .single-plot .channel { width:100%; height:100%; border:0 } .view-switcher { position:relative; display:flex; flex-direction:column; height:100%; min-height:0 } .view-pane { width:100%; flex:1; min-height:0 } .view-pane:not(.active) { position:absolute; inset:40px 0 0; visibility:hidden; pointer-events:none } .view-switcher-head { position:relative; z-index:2; flex:none; height:40px; display:flex; align-items:center; gap:10px; padding:4px 8px; border-bottom:1px solid var(--line); background:white } .view-switcher-label { color:var(--muted); font-size:12px; font-weight:600 } .view-choice { border:1px solid var(--line); border-radius:5px; background:white; padding:4px 9px; color:var(--muted); font:12px inherit; cursor:pointer } .view-choice.active { border-color:var(--accent); background:#e8f3f3; color:#17626a } .view-switcher-select { min-height:30px; min-width:150px; padding:4px 28px 4px 8px; border:1px solid var(--line); border-radius:5px; background:white; color:var(--ink); font:12px inherit } .channel { min-width:0; min-height:0; height:100%; overflow:hidden; border-right:1px solid var(--line); border-bottom:1px solid var(--line); background:white } .channel:nth-child(2n) { border-right:0 } .layout-column { display:flex; flex-direction:column; gap:8px; height:100%; min-height:0; overflow:auto } .layout-column > .plotly-view,.layout-column > .matplotlib-view { flex:1 } .layout-row { display:flex; gap:8px; height:100%; min-height:0 } .layout-panel { height:100%; min-height:0; overflow:auto; padding:12px; border:1px solid var(--line); border-radius:7px } .prose,.text-view { padding:16px; color:var(--ink) } .prose h1,.prose h2,.prose h3 { margin:0 0 8px } .table-wrap { overflow:auto; padding:8px } .data-table { width:100%; border-collapse:collapse; font-size:12px } .data-table th { position:sticky; top:0; background:var(--wash); color:var(--muted); text-align:left } .data-table th,.data-table td { padding:7px 9px; border-bottom:1px solid var(--line); white-space:nowrap } .empty,.error { padding:36px; text-align:center; color:var(--muted); border:1px dashed #bac9cd; border-radius:10px }
    .error { color:#8c2e2e; background:#fff7f7 } @media(max-width:700px){header{padding:0 14px}header span{display:none}main{margin-top:20px}main.item-page{width:calc(100% - 12px);margin-top:6px}.toolbar{flex-wrap:wrap}.playback-grid{grid-template-columns:1fr;grid-template-rows:repeat(var(--grid-items),minmax(0,1fr))}.channel{border-right:0}.card{grid-template-columns:1fr}.card-tags{text-align:left}.item-title .subtitle{display:block;margin:2px 0 0}.data-toolbar{flex-wrap:wrap}.analysis-panel{right:auto;left:0;min-width:min(340px,calc(100vw - 24px))}}
  </style>
</head>
<body><header><b>Scientific Workspace Browser</b><span>Explore scientific and analytical results</span></header><main id="app"><div class="empty">Loading workspaces…</div></main>
<script src="/assets/plotly.min.js"></script>
<script>
const app=document.querySelector('#app');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=async path=>{const r=await fetch(path);if(!r.ok)throw new Error((await r.json()).detail||`Request failed (${r.status})`);return r.json()};
const fail=e=>app.innerHTML=`<div class="error"><b>Unable to load this page</b><br>${esc(e.message)}</div>`;
let playbackTimer=null,playbackPosition=0,playbackPaused=false,plotResizeObserver=null,dataStageResizeFrame=null;
const viewSelections={};
function markdown(value){return esc(value).replace(/^### (.+)$/gm,'<h3>$1</h3>').replace(/^## (.+)$/gm,'<h2>$1</h2>').replace(/^# (.+)$/gm,'<h1>$1</h1>').replace(/\n/g,'<br>')}
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
function stopPlayback(){clearInterval(playbackTimer);playbackTimer=null;plotResizeObserver?.disconnect();plotResizeObserver=null;cancelAnimationFrame(dataStageResizeFrame);dataStageResizeFrame=null;window.removeEventListener('resize',sizeDataStage)}
function tableRows(value){if(Array.isArray(value))return value;if(!value||typeof value!=='object')return[];const columns=Object.keys(value),indices=[...new Set(columns.flatMap(column=>Object.keys(value[column]||{})))];return indices.map(index=>Object.fromEntries(columns.map(column=>[column,value[column]?.[index]])))}
function tableHtml(value){const rows=tableRows(value);if(!rows.length)return '<div class="empty">No rows</div>';const columns=[...new Set(rows.flatMap(row=>Object.keys(row)))];return `<div class="table-wrap"><table class="data-table"><thead><tr>${columns.map(column=>`<th>${esc(column)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${columns.map(column=>`<td>${esc(statText(row[column]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}
function renderView(v){if(v.kind==='plotly')return plotlyFigure(v.value,v.name);if(v.kind==='matplotlib')return matplotlibFigure(v.value,v.name);if(v.kind==='markdown')return `<article class="prose">${markdown(v.value)}</article>`;if(v.kind==='text')return `<div class="text-view">${esc(v.value)}</div>`;if(v.kind==='table'||v.kind==='dataframe')return tableHtml(v.value);return `<pre>${esc(typeof v.value==='string'?v.value:JSON.stringify(v.value,null,2))}</pre>`}
function gridTemplate(columns){if(Array.isArray(columns))return columns.map(weight=>`minmax(0,${Number(weight)||1}fr)`).join(' ');const count=Math.max(1,Number(columns)||1);return `repeat(${count},minmax(0,1fr))`}
function renderLayout(node,views){if(node.kind==='view_slot'){const view=views.find(v=>v.name===node.view);return view?renderView(view):''}if(node.kind==='tabs'){const labels=node.children.map((child,i)=>child.props.label||`Tab ${i+1}`);return `<div class="layout-tabs" data-layout-tabs><nav class="tabs">${labels.map((label,i)=>`<button class="tab ${i===0?'active':''}" data-layout-tab="${i}">${esc(label)}</button>`).join('')}</nav><div class="layout-tab-panes">${node.children.map((child,i)=>`<div class="layout-tab-pane ${i===0?'active':''}" data-layout-pane="${i}" aria-hidden="${i!==0}">${renderLayout(child,views)}</div>`).join('')}</div></div>`}if(node.kind==='view_switcher'){const key=String(node.props.key),selected=viewSelections[key]||0,labels=node.children.map((child,i)=>child.props.label||`View ${i+1}`),selector=node.props.selector||'buttons',control=selector==='dropdown'?`<select class="view-switcher-select" data-view-select>${labels.map((label,i)=>`<option value="${i}" ${i===selected?'selected':''}>${esc(label)}</option>`).join('')}</select>`:labels.map((label,i)=>`<button class="view-choice ${i===selected?'active':''}" data-view-choice="${i}">${esc(label)}</button>`).join('');return `<div class="view-switcher" data-view-switcher="${esc(key)}"><div class="view-switcher-head"><span class="view-switcher-label">${esc(node.props.label||'View')}</span>${control}</div>${node.children.map((child,i)=>`<div class="view-pane ${i===selected?'active':''}" data-view-pane="${i}" aria-hidden="${i!==selected}">${renderLayout(child,views)}</div>`).join('')}</div>`}const children=node.children.map(child=>renderLayout(child,views)).join('');if(node.kind==='grid'){const columnCount=Array.isArray(node.props.columns)?node.props.columns.length:Number(node.props.columns)||1,rowCount=Math.ceil(node.children.length/columnCount);return `<div class="playback-grid ${node.children.length===1?'single-plot':''}" style="--grid-template:${gridTemplate(node.props.columns)};--grid-rows:repeat(${rowCount},minmax(0,1fr));--grid-items:${node.children.length}">${node.children.map(child=>`<div class="channel">${renderLayout(child,views)}</div>`).join('')}</div>`}if(node.kind==='column'||node.kind==='stack')return `<div class="layout-column">${children}</div>`;if(node.kind==='row')return `<div class="layout-row">${children}</div>`;if(node.kind==='panel')return `<div class="layout-panel">${children}</div>`;return children}
function bindLayoutTabs(){document.querySelectorAll('[data-layout-tabs]').forEach(root=>{const buttons=root.querySelectorAll(':scope > .tabs > [data-layout-tab]'),panes=root.querySelectorAll(':scope > .layout-tab-panes > [data-layout-pane]');buttons.forEach(button=>button.onclick=()=>{const selected=Number(button.dataset.layoutTab);buttons.forEach((candidate,index)=>candidate.classList.toggle('active',index===selected));panes.forEach((pane,index)=>{pane.classList.toggle('active',index===selected);pane.setAttribute('aria-hidden',String(index!==selected))});requestAnimationFrame(resizePlots)})})}
function bindViewSwitchers(){document.querySelectorAll('[data-view-switcher]').forEach(root=>{const activate=selected=>{viewSelections[root.dataset.viewSwitcher]=selected;root.querySelectorAll('[data-view-choice]').forEach((choice,index)=>choice.classList.toggle('active',index===selected));root.querySelectorAll('[data-view-pane]').forEach((pane,index)=>{pane.classList.toggle('active',index===selected);pane.setAttribute('aria-hidden',String(index!==selected))});const select=root.querySelector('[data-view-select]');if(select)select.value=selected};root.querySelectorAll('[data-view-choice]').forEach(button=>button.onclick=()=>activate(Number(button.dataset.viewChoice)));const select=root.querySelector('[data-view-select]');if(select)select.onchange=()=>activate(Number(select.value))})}
function startFrameworkPlayback(config,refresh){const bar=document.querySelector('#playback-bar');if(!bar)return;clearInterval(playbackTimer);if(playbackPosition>config.duration_seconds)playbackPosition=0;let updating=false;const updateClock=()=>{bar.querySelector('#position').value=playbackPosition;bar.querySelector('#counter').textContent=`${playbackPosition.toFixed(2)} / ${config.duration_seconds.toFixed(2)} s`},update=async()=>{if(updating)return;updating=true;try{await refresh()}finally{updating=false}};bar.querySelector('#position').max=config.duration_seconds;bar.querySelector('#position').step=config.step_seconds;bar.querySelector('#position').oninput=async e=>{playbackPosition=Number(e.target.value);updateClock();await update()};bar.querySelector('#toggle').onclick=()=>{playbackPaused=!playbackPaused;bar.querySelector('#toggle').textContent=playbackPaused?'▶ Play':'❚❚ Pause'};updateClock();const interval=config.refresh_interval_seconds??config.step_seconds;playbackTimer=setInterval(async()=>{if(playbackPaused||updating)return;playbackPosition+=config.step_seconds;if(playbackPosition>config.duration_seconds)playbackPosition=config.loop?0:config.duration_seconds;updateClock();await update()},interval*1000)}
function startFrameworkRefresh(config,refresh){let updating=false;playbackTimer=setInterval(async()=>{if(updating)return;updating=true;try{await refresh()}finally{updating=false}},config.interval_seconds*1000)}
const controlLabel=name=>name.split('_').map(x=>x[0].toUpperCase()+x.slice(1)).join(' ');
function controlHtml(control,values){const value=values[control.name]??control.default;if(control.control_type==='select')return `<select data-control="${esc(control.name)}">${control.options.map(option=>`<option value="${esc(option)}" ${String(value)===String(option)?'selected':''}>${esc(option)}</option>`).join('')}</select>`;if(control.control_type==='integer'||control.control_type==='float')return `<input type="number" data-control="${esc(control.name)}" value="${esc(value)}" ${control.minimum==null?'':`min="${esc(control.minimum)}"`} ${control.maximum==null?'':`max="${esc(control.maximum)}"`} ${control.step==null?'':`step="${esc(control.step)}"`}>`;return `<input data-control="${esc(control.name)}" value="${esc(value)}">`}
const statText=value=>value!=null&&typeof value==='object'?JSON.stringify(value):String(value??'—');
function statisticsRows(statistics){return Object.entries(statistics||{}).map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(statText(value))}</dd></div>`).join('')}
function controlsHtml(controls,values,statistics){if(!controls.length&&!Object.keys(statistics||{}).length)return'';return `<details class="control-drawer"><summary>Analysis settings</summary><div class="analysis-panel"><div class="control-fields">${controls.map(c=>`<label>${esc(controlLabel(c.name))}${controlHtml(c,values)}</label>`).join('')}</div><dl class="view-stats" id="view-stats">${statisticsRows(statistics)}<div><dt>Plotly render</dt><dd data-client-stat="plotly-runtime">—</dd></div></dl></div></details>`}
function updateStatistics(statistics){const target=document.querySelector('#view-stats');if(target)target.innerHTML=`${statisticsRows(statistics)}<div><dt>Plotly render</dt><dd data-client-stat="plotly-runtime">—</dd></div>`}
async function catalog(navigate=true){stopPlayback();app.className='';if(navigate)history.pushState(null,'','/');try{const {workspaces}=await api('/workspaces');app.innerHTML=`<h1>Workspaces</h1><p class="lead">Choose a workspace to discover its available items.</p><div class="list">${workspaces.map(w=>`<article class="card" data-id="${esc(w.id)}"><div><span class="tag">${esc(w.category||'workspace')}</span><h2>${esc(w.name)}</h2></div><p class="muted">${esc(w.description)}</p><div class="card-tags">${w.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div></article>`).join('')}</div>`;document.querySelectorAll('.card').forEach(x=>x.onclick=()=>items(x.dataset.id,workspaces.find(w=>w.id===x.dataset.id).name))}catch(e){fail(e)}}
async function items(id,name,navigate=true){stopPlayback();app.className='';if(navigate)history.pushState(null,'',`/workspace/${encodeURIComponent(id)}`);app.innerHTML='<div class="empty">Discovering items…</div>';try{const {items:list}=await api(`/workspaces/${encodeURIComponent(id)}/items`);app.innerHTML=`<div class="crumb"><button id="home">Workspaces</button> / ${esc(name)}</div><h1>${esc(name)}</h1><p class="lead">Browse and open discovered items.</p><div class="toolbar"><input id="search" type="search" placeholder="Search items…"><button class="primary" id="refresh">Refresh list</button></div><div class="list" id="items"></div>`;const draw=()=>{const q=document.querySelector('#search').value.toLowerCase();const shown=list.filter(x=>!q||`${x.title} ${x.subtitle||''} ${x.tags.join(' ')}`.toLowerCase().includes(q));document.querySelector('#items').innerHTML=shown.length?shown.map(x=>`<article class="card" data-item="${esc(x.id)}"><div><h2>${esc(x.title)}</h2></div><p class="muted">${esc(x.subtitle||'')}</p><div class="card-tags">${x.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div></article>`).join(''):'<div class="empty">No matching items.</div>';document.querySelectorAll('[data-item]').forEach(x=>x.onclick=()=>openItem(id,name,x.dataset.item))};draw();document.querySelector('#home').onclick=()=>catalog();document.querySelector('#search').oninput=draw;document.querySelector('#refresh').onclick=()=>items(id,name,false)}catch(e){fail(e)}}
async function openItem(wid,wname,iid,navigate=true,controlValues={},preservePlayback=false){
  stopPlayback();app.className='item-page';if(!preservePlayback){playbackPosition=0;playbackPaused=false;Object.keys(viewSelections).forEach(key=>delete viewSelections[key])}if(navigate)history.pushState(null,'',`/workspace/${encodeURIComponent(wid)}/item/${encodeURIComponent(iid)}`);app.innerHTML='<div class="empty">Opening item…</div>';
  try{const request=async values=>api(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}?${new URLSearchParams(values)}`);let data=await request({...controlValues,__playback_time_seconds:playbackPosition}),p=data.page,requestGeneration=0;const isPlayback=p.playback.enabled;
    app.innerHTML=`<section class="item-header"><div class="crumb"><button id="home">Workspaces</button> / <button id="back">${esc(wname)}</button></div><div class="item-title"><h1>${esc(p.title)}</h1><span class="subtitle">${esc(p.subtitle||'')}</span></div></section>${isPlayback||p.controls.length||Object.keys(p.statistics||{}).length?`<div class="data-toolbar">${isPlayback?`<div class="playback-bar" id="playback-bar"><button class="primary" id="toggle">${playbackPaused?'▶ Play':'❚❚ Pause'}</button><input id="position" aria-label="Playback position" type="range" min="0" value="${playbackPosition}"><span id="counter"></span></div>`:'<span></span>'}${controlsHtml(p.controls,p.control_values,p.statistics)}</div>`:''}<section class="data-stage"><div id="active-view" class="view"></div></section>`;
    document.querySelector('#active-view').innerHTML=renderLayout(p.layout,p.rendered_views);bindLayoutTabs();bindViewSwitchers();observeDataStage();void initializePlotlyViews(p.rendered_views);requestAnimationFrame(resizePlots);
    const selected=()=>Object.fromEntries([...document.querySelectorAll('[data-control]')].map(c=>[c.dataset.control,c.value]));
    const refresh=async(includeStatic=false)=>{const generation=++requestGeneration,result=await request({...selected(),__playback_time_seconds:playbackPosition,__include_static_views:includeStatic});if(generation!==requestGeneration)return false;data=result;p=data.page;updateStatistics(p.statistics);await updatePlotlyViews(p.rendered_views);updateMatplotlibViews(p.rendered_views);return true};
    const settingsChanged=async()=>{if(isPlayback)clearInterval(playbackTimer);const applied=await refresh(true);if(isPlayback&&applied)startFrameworkPlayback(p.playback,refresh)};
    if(isPlayback)startFrameworkPlayback(p.playback,refresh);else if(p.refresh.enabled)startFrameworkRefresh(p.refresh,refresh);document.querySelectorAll('[data-control]').forEach(x=>x.onchange=settingsChanged);document.querySelector('#home').onclick=()=>catalog();document.querySelector('#back').onclick=()=>items(wid,wname)
  }catch(e){fail(e)}}
async function boot(){const parts=location.pathname.split('/').filter(Boolean).map(decodeURIComponent);if(parts[0]!=='workspace')return catalog(false);try{const {workspaces}=await api('/workspaces'),workspace=workspaces.find(w=>w.id===parts[1]);if(!workspace)return catalog(false);if(parts[2]==='item'&&parts[3])return openItem(workspace.id,workspace.name,parts[3],false);return items(workspace.id,workspace.name,false)}catch(e){fail(e)}}
window.onpopstate=boot;boot();
</script></body></html>"""


@dataclass(frozen=True)
class WorkspaceModuleRegistration:
    module_name: str
    attribute: str
    watch_path: Path | None = None
    configuration: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkspaceBrowserApp:
    title: str = "Scientific Workspace Browser"
    registry: WorkspaceRegistry | None = None
    reload_workspaces: bool = False
    workspace_modules: tuple[WorkspaceModuleRegistration, ...] = ()
    _fixed_workspaces: list[Any] = field(default_factory=list, init=False, repr=False)
    _workspace_snapshot: dict[Path, int] = field(default_factory=dict, init=False, repr=False)
    _reload_lock: RLock = field(default_factory=RLock, init=False, repr=False)

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
                "playback": opened.page.playback.__dict__,
                "refresh": opened.page.refresh.__dict__,
                "statistics": statistics,
                "views": [view.name for view in opened.page.views],
                "rendered_views": rendered_views,
                "layout": _layout_to_dict(opened.page.layout),
                "metadata": opened.page.metadata,
                "actions": list(opened.page.actions),
            },
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
    title: str = "Scientific Workspace Browser",
    *,
    reload_workspaces: bool = True,
    config_path: str | Path | None = None,
) -> WorkspaceBrowserApp:
    if config_path is not None:
        profile = load_browser_profile(config_path)
        return WorkspaceBrowserApp(
            title=profile.title or title,
            reload_workspaces=reload_workspaces,
            workspace_modules=tuple(_profile_registration(spec) for spec in profile.workspaces),
        )
    return WorkspaceBrowserApp(
        title=title,
        reload_workspaces=reload_workspaces,
        workspace_modules=(
            WorkspaceModuleRegistration("workspace_browser.examples.generic", "GenericExampleWorkspace"),
            WorkspaceModuleRegistration("workspace_browser.examples.sigmf", "create_workspace"),
            WorkspaceModuleRegistration("workspace_browser.examples.sigmf_matplotlib", "create_workspace"),
            WorkspaceModuleRegistration("workspace_browser.examples.pri", "create_workspace"),
        ),
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

        # BaseHTTPRequestHandler requires this exact method name.
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/assets/plotly.min.js":
                self._write_javascript(_PLOTLY_JS)
                return
            if parsed.path == "/" or parsed.path.startswith("/workspace/"):
                self._write_html(_INDEX_HTML.replace("Scientific Workspace Browser", app.title))
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
                if len(parts) == 3 and parts[0] == "workspaces" and parts[2] == "items":
                    query = parse_qs(parsed.query)
                    self._write_json(200, {"items": app.list_items(parts[1], query)})
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
