from __future__ import annotations


def list_routes() -> list[str]:
    return [
        "/health",
        "/workspaces",
        "/workspaces/{workspace_id}/items",
        "/workspaces/{workspace_id}/items/{item_id}",
    ]
