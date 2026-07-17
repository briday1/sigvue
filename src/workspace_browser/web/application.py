from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from plotly.offline import get_plotlyjs

from workspace_browser.catalog.browser import filter_items, paginate_items, search_items, sort_items
from workspace_browser.examples.generic import GenericExampleWorkspace
from workspace_browser.registry.registry import WorkspaceRegistry
from workspace_browser.rendering.dispatch import detect_render_kind


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
    header { height:64px; display:flex; align-items:center; gap:14px; padding:0 28px; color:white; background:#102f3a; box-shadow:0 2px 10px #102f3a33 }
    header b { font-size:18px } header span { color:#b9d0d5 }
    main { width:min(1120px,calc(100% - 36px)); margin:34px auto 80px }
    .crumb { color:var(--muted); margin-bottom:20px } .crumb button { all:unset; cursor:pointer; color:var(--accent) }
    h1 { margin:0 0 6px; font-size:30px; letter-spacing:-.02em } .lead { color:var(--muted); margin:0 0 28px }
    .toolbar { display:flex; gap:10px; margin:24px 0 } input,select { min-height:42px; border:1px solid #bdcbd0; border-radius:7px; padding:8px 12px; background:white; font:inherit }
    input[type=search] { flex:1 } button.primary { border:0; border-radius:7px; padding:10px 15px; color:white; background:var(--accent); font:600 14px inherit; cursor:pointer }
    .list { display:flex; flex-direction:column; gap:10px }
    .card { display:grid; grid-template-columns:minmax(180px,1fr) 2fr auto; align-items:center; gap:18px; border:1px solid var(--line); border-radius:8px; background:white; padding:16px 18px; box-shadow:0 2px 8px #17323c0b; cursor:pointer; transition:.15s }
    .card:hover { border-color:#8eb9bf; box-shadow:0 4px 14px #17323c14 } .card h2 { font-size:17px; margin:4px 0 } .card p { margin:0 } .card-tags { text-align:right; min-width:130px }
    .muted { color:var(--muted) } .tag,.status { display:inline-block; border-radius:999px; padding:3px 9px; margin:2px 4px 2px 0; font-size:12px; background:#e8f3f3; color:#17626a }
    .status { background:#e6f5ec; color:#247044; text-transform:capitalize } .panel { border:1px solid var(--line); border-radius:10px; background:white; padding:24px; margin-top:22px }
    .tabs { display:flex; gap:6px; border-bottom:1px solid var(--line); margin:-8px -4px 20px } .tab { border:0; border-bottom:3px solid transparent; background:none; padding:11px 14px; color:var(--muted); font:600 14px inherit; cursor:pointer } .tab.active { color:var(--accent); border-color:var(--accent) }
    .plot-controls { display:flex; align-items:end; gap:12px; flex-wrap:wrap; padding:14px; margin:18px 0 10px; border:1px solid var(--line); border-radius:8px; background:white } .plot-controls label { display:flex; flex-direction:column; gap:4px; color:var(--muted); font-size:12px } .plot-controls select { min-height:36px; padding:5px 28px 5px 9px; color:var(--ink) }
    .view h1 { font-size:24px } .plotly-view { width:100%; height:330px } .playback-bar { display:flex; align-items:center; gap:12px; padding:10px 12px; margin-bottom:16px; border-radius:8px; background:var(--wash) } .playback-bar input { flex:1; min-height:auto } .playback-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px } .channel { border:1px solid var(--line); border-radius:8px; padding:6px; overflow:hidden } .empty,.error { padding:36px; text-align:center; color:var(--muted); border:1px dashed #bac9cd; border-radius:10px }
    .error { color:#8c2e2e; background:#fff7f7 } @media(max-width:700px){header{padding:0 18px}header span{display:none}main{margin-top:24px}.toolbar{flex-wrap:wrap}.playback-grid{grid-template-columns:1fr}.card{grid-template-columns:1fr}.card-tags{text-align:left}}
  </style>
</head>
<body><header><b>Scientific Workspace Browser</b><span>Explore scientific and analytical results</span></header><main id="app"><div class="empty">Loading workspaces…</div></main>
<script src="/assets/plotly.min.js"></script>
<script>
const app=document.querySelector('#app');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=async path=>{const r=await fetch(path);if(!r.ok)throw new Error((await r.json()).detail||`Request failed (${r.status})`);return r.json()};
const fail=e=>app.innerHTML=`<div class="error"><b>Unable to load this page</b><br>${esc(e.message)}</div>`;
let playbackTimer=null,playbackPosition=0,playbackPaused=false;
function markdown(value){return esc(value).replace(/^### (.+)$/gm,'<h3>$1</h3>').replace(/^## (.+)$/gm,'<h2>$1</h2>').replace(/^# (.+)$/gm,'<h1>$1</h1>').replace(/\n/g,'<br>')}
function plotlyFigure(figure,viewName){const id=`plotly-${encodeURIComponent(viewName)}`;setTimeout(()=>{const target=document.getElementById(id);if(target)Plotly.newPlot(target,figure.data||[],figure.layout||{},{responsive:true,displaylogo:false})},0);return `<div id="${id}" class="plotly-view" data-plot-view="${esc(viewName)}"></div>`}
function updatePlotlyViews(views){document.querySelectorAll('[data-plot-view]').forEach(target=>{const view=views.find(candidate=>candidate.name===target.dataset.plotView);if(view&&view.kind==='plotly')Plotly.react(target,view.value.data||[],view.value.layout||{},{responsive:true,displaylogo:false})})}
function stopPlayback(){clearInterval(playbackTimer);playbackTimer=null}
function renderView(v){if(v.kind==='plotly')return plotlyFigure(v.value,v.name);if(v.kind==='markdown')return markdown(v.value);return `<pre>${esc(typeof v.value==='string'?v.value:JSON.stringify(v.value,null,2))}</pre>`}
function renderLayout(node,views,selectedTab=0){if(node.kind==='view_slot'){const view=views.find(v=>v.name===node.view);return view?renderView(view):''}if(node.kind==='tabs'){const labels=node.children.map((child,i)=>child.props.label||`Tab ${i+1}`);return `<nav class="tabs">${labels.map((label,i)=>`<button class="tab ${i===selectedTab?'active':''}" data-layout-tab="${i}">${esc(label)}</button>`).join('')}</nav>${renderLayout(node.children[selectedTab]||node.children[0],views,selectedTab)}`}const children=node.children.map(child=>renderLayout(child,views,selectedTab)).join('');return node.kind==='grid'?`<div class="playback-grid">${node.children.map(child=>`<div class="channel">${renderLayout(child,views,selectedTab)}</div>`).join('')}</div>`:children}
function startFrameworkPlayback(config,refresh){const bar=document.querySelector('#playback-bar');if(!bar)return;let updating=false;const updateClock=()=>{bar.querySelector('#position').value=playbackPosition;bar.querySelector('#counter').textContent=`${playbackPosition.toFixed(2)} s`},update=async()=>{if(updating)return;updating=true;try{await refresh()}finally{updating=false}};bar.querySelector('#position').max=config.duration_seconds;bar.querySelector('#position').step=config.step_seconds;bar.querySelector('#position').oninput=async e=>{playbackPosition=Number(e.target.value);updateClock();await update()};bar.querySelector('#toggle').onclick=()=>{playbackPaused=!playbackPaused;bar.querySelector('#toggle').textContent=playbackPaused?'▶ Play':'❚❚ Pause'};updateClock();playbackTimer=setInterval(async()=>{if(playbackPaused||updating)return;playbackPosition+=config.step_seconds;if(playbackPosition>config.duration_seconds)playbackPosition=config.loop?0:config.duration_seconds;updateClock();await update()},config.step_seconds*1000)}
const controlLabel=name=>name.split('_').map(x=>x[0].toUpperCase()+x.slice(1)).join(' ');
function controlsHtml(controls,values){if(!controls.length)return'';return `<div class="plot-controls"><b>Plot controls</b>${controls.map(c=>`<label>${esc(controlLabel(c.name))}<select data-control="${esc(c.name)}">${c.options.map(option=>`<option value="${esc(option)}" ${String(values[c.name])===String(option)?'selected':''}>${esc(option)}</option>`).join('')}</select></label>`).join('')}</div>`}
async function catalog(navigate=true){stopPlayback();if(navigate)history.pushState(null,'','/');try{const {workspaces}=await api('/workspaces');app.innerHTML=`<h1>Workspaces</h1><p class="lead">Choose a workspace to discover its available items.</p><div class="list">${workspaces.map(w=>`<article class="card" data-id="${esc(w.id)}"><div><span class="tag">${esc(w.category||'workspace')}</span><h2>${esc(w.name)}</h2></div><p class="muted">${esc(w.description)}</p><div class="card-tags">${w.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div></article>`).join('')}</div>`;document.querySelectorAll('.card').forEach(x=>x.onclick=()=>items(x.dataset.id,workspaces.find(w=>w.id===x.dataset.id).name))}catch(e){fail(e)}}
async function items(id,name,navigate=true){stopPlayback();if(navigate)history.pushState(null,'',`/workspace/${encodeURIComponent(id)}`);app.innerHTML='<div class="empty">Discovering items…</div>';try{const {items:list}=await api(`/workspaces/${encodeURIComponent(id)}/items`);app.innerHTML=`<div class="crumb"><button id="home">Workspaces</button> / ${esc(name)}</div><h1>${esc(name)}</h1><p class="lead">Browse and open discovered items.</p><div class="toolbar"><input id="search" type="search" placeholder="Search items…"><select id="status"><option value="">All statuses</option>${[...new Set(list.map(x=>x.status))].map(x=>`<option>${esc(x)}</option>`).join('')}</select><button class="primary" id="refresh">Refresh list</button></div><div class="list" id="items"></div>`;const draw=()=>{const q=document.querySelector('#search').value.toLowerCase(),s=document.querySelector('#status').value;const shown=list.filter(x=>(!q||`${x.title} ${x.subtitle||''} ${x.tags.join(' ')}`.toLowerCase().includes(q))&&(!s||x.status===s));document.querySelector('#items').innerHTML=shown.length?shown.map(x=>`<article class="card" data-item="${esc(x.id)}"><div><span class="status">${esc(x.status)}</span><h2>${esc(x.title)}</h2></div><p class="muted">${esc(x.subtitle||'')}</p><div class="card-tags">${x.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div></article>`).join(''):'<div class="empty">No matching items.</div>';document.querySelectorAll('[data-item]').forEach(x=>x.onclick=()=>openItem(id,name,x.dataset.item))};draw();document.querySelector('#home').onclick=()=>catalog();document.querySelector('#search').oninput=draw;document.querySelector('#status').onchange=draw;document.querySelector('#refresh').onclick=()=>items(id,name,false)}catch(e){fail(e)}}
async function openItem(wid,wname,iid,navigate=true,controlValues={},preservePlayback=false){
  stopPlayback();if(!preservePlayback){playbackPosition=0;playbackPaused=false}if(navigate)history.pushState(null,'',`/workspace/${encodeURIComponent(wid)}/item/${encodeURIComponent(iid)}`);app.innerHTML='<div class="empty">Opening item…</div>';
  try{const request=async values=>api(`/workspaces/${encodeURIComponent(wid)}/items/${encodeURIComponent(iid)}?${new URLSearchParams(values)}`);let data=await request({...controlValues,__playback_time_seconds:playbackPosition}),p=data.page,activeTab=0;const isPlayback=p.playback.enabled;
    app.innerHTML=`<div class="crumb"><button id="home">Workspaces</button> / <button id="back">${esc(wname)}</button> / ${esc(p.title)}</div><span class="status">${esc(p.status)}</span><h1>${esc(p.title)}</h1><p class="lead">${esc(p.subtitle||'')}</p>${controlsHtml(p.controls,p.control_values)}${isPlayback?`<div class="playback-bar" id="playback-bar"><button class="primary" id="toggle">${playbackPaused?'▶ Play':'❚❚ Pause'}</button><input id="position" type="range" min="0" value="${playbackPosition}"><span id="counter"></span></div>`:''}<section class="panel"><div id="active-view" class="view"></div></section>`;
    const show=i=>{activeTab=i;document.querySelector('#active-view').innerHTML=renderLayout(p.layout,p.rendered_views,activeTab);document.querySelectorAll('[data-layout-tab]').forEach(x=>x.onclick=()=>show(Number(x.dataset.layoutTab)))};show(0);
    const selected=()=>Object.fromEntries([...document.querySelectorAll('[data-control]')].map(c=>[c.dataset.control,c.value]));
    const refresh=async()=>{data=await request({...selected(),__playback_time_seconds:playbackPosition});p=data.page;updatePlotlyViews(p.rendered_views)};
    if(isPlayback)startFrameworkPlayback(p.playback,refresh);document.querySelectorAll('[data-control]').forEach(x=>x.onchange=refresh);document.querySelector('#home').onclick=()=>catalog();document.querySelector('#back').onclick=()=>items(wid,wname)
  }catch(e){fail(e)}}
async function boot(){const parts=location.pathname.split('/').filter(Boolean).map(decodeURIComponent);if(parts[0]!=='workspace')return catalog(false);try{const {workspaces}=await api('/workspaces'),workspace=workspaces.find(w=>w.id===parts[1]);if(!workspace)return catalog(false);if(parts[2]==='item'&&parts[3])return openItem(workspace.id,workspace.name,parts[3],false);return items(workspace.id,workspace.name,false)}catch(e){fail(e)}}
window.onpopstate=boot;boot();
</script></body></html>"""


@dataclass
class WorkspaceBrowserApp:
    title: str = "Scientific Workspace Browser"
    registry: WorkspaceRegistry | None = None

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = WorkspaceRegistry()

    def register_workspace(self, workspace: Any) -> None:
        self.registry.register(workspace)

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
        statuses = set(filter(None, query_params.get("status", [])))
        tags = set(filter(None, query_params.get("tag", [])))
        sort_by = query_params.get("sort", ["title"])[0]
        descending = query_params.get("desc", ["0"])[0] == "1"
        page = int(query_params.get("page", ["1"])[0])
        page_size = int(query_params.get("page_size", ["50"])[0])

        filtered = filter_items(search_items(items, query), statuses=statuses, tags=tags)
        sorted_items = sort_items(filtered, by=sort_by, descending=descending)
        paged = paginate_items(sorted_items, page=page, page_size=page_size)

        return [
            {
                "id": item.identifier,
                "title": item.title,
                "subtitle": item.subtitle,
                "status": item.status.value,
                "source_reference": item.source_reference,
                "timestamp": item.timestamp.isoformat() if item.timestamp else None,
                "tags": list(item.tags),
                "summary_fields": item.summary_fields,
            }
            for item in paged
        ]

    def open_item(self, workspace_id: str, item_id: str, control_values: dict[str, object] | None = None) -> dict[str, Any]:
        workspace = self.registry.get(workspace_id)
        opened = workspace.open_item(item_id)
        opened.page.validate()
        values = {control.name: control.default for control in opened.page.controls}
        values.update(control_values or {})
        rendered_views = []
        for view in opened.page.views:
            value = view.callback(values)
            kind = detect_render_kind(value).value
            rendered_views.append({"name": view.name, "kind": kind, "value": _json_value(value)})
        return {
            "item": {
                "id": opened.item.identifier,
                "title": opened.item.title,
                "status": opened.item.status.value,
            },
            "page": {
                "title": opened.page.title,
                "subtitle": opened.page.subtitle,
                "status": opened.page.status,
                "controls": [control.__dict__ for control in opened.page.controls],
                "control_values": values,
                "playback": opened.page.playback.__dict__,
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


def create_app(title: str = "Scientific Workspace Browser") -> WorkspaceBrowserApp:
    app = WorkspaceBrowserApp(title=title)
    app.register_workspace(GenericExampleWorkspace())
    return app


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
                self._write_json(200, {"workspaces": app.list_workspaces()})
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
            except Exception:  # pragma: no cover
                self._write_json(500, {"error": "internal_error"})
                return

            self._write_json(404, {"error": "not_found"})

        def log_message(self, message_format: str, *args: Any) -> None:
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Scientific Workspace Browser server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()

    app = create_app()
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
