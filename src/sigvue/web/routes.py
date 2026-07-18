from __future__ import annotations


def list_routes() -> list[str]:
    return [
        "/",
        "/workspace/{workspace_id}",
        "/workspace/{workspace_id}/item/{item_id}",
        "/health",
        "/workspaces",
        "/workspaces/{workspace_id}/items",
        "/workspaces/{workspace_id}/items/{item_id}",
    ]
