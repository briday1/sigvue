# Scientific Workspace Browser

Pure-Python framework for registering scientific workspaces, discovering viewable items, opening workspace-defined item pages, and serving a browser-oriented API.

Launching the service now opens a browser interface at `/`; the JSON API remains available for integrations.

## What is included

- Workspace contract, metadata, item descriptors, and normalized statuses.
- Direct workspace registration and optional package entry-point discovery with failure isolation.
- Workspace catalog + item browser utilities (search, filtering, sorting, grouping, pagination).
- Declarative layout primitives (`tabs`, `row`, `column`, `grid`, `panel`, `split_pane`, `sidebar`, etc.).
- Renderable dispatch for Plotly, Matplotlib, DataFrame/table/text/image/download content types.
- Refresh manager with overlap prevention and stale-result rejection.
- Minimal HTTP service with:
  - `/health`
  - `/workspaces`
  - `/workspaces/{workspace_id}/items`
  - `/workspaces/{workspace_id}/items/{item_id}`
- Generic example workspace.

## Run

```bash
PYTHONPATH=src python -m workspace_browser.web.application --host 127.0.0.1 --port 8000
```

Or install and run:

```bash
pip install -e .
workspace-browser
```

## Test

```bash
PYTHONPATH=src python -m unittest discover -s tests -q
```
