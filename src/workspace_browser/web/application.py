from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from workspace_browser.catalog.browser import filter_items, paginate_items, search_items, sort_items
from workspace_browser.examples.generic import GenericExampleWorkspace
from workspace_browser.registry.registry import WorkspaceRegistry


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

    def open_item(self, workspace_id: str, item_id: str) -> dict[str, Any]:
        workspace = self.registry.get(workspace_id)
        opened = workspace.open_item(item_id)
        opened.page.validate()
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
                "views": [view.name for view in opened.page.views],
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


def create_app(title: str = "Scientific Workspace Browser") -> WorkspaceBrowserApp:
    app = WorkspaceBrowserApp(title=title)
    app.register_workspace(GenericExampleWorkspace())
    return app


def _make_handler(app: WorkspaceBrowserApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
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
                    self._write_json(200, app.open_item(parts[1], parts[3]))
                    return
            except KeyError:
                self._write_json(404, {"error": "workspace_not_found"})
                return
            except Exception as exc:  # pragma: no cover
                self._write_json(500, {"error": str(exc)})
                return

            self._write_json(404, {"error": "not_found"})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
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
