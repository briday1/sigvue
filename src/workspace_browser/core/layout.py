from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .errors import InvalidLayoutError


@dataclass(frozen=True)
class LayoutNode:
    kind: str
    children: tuple["LayoutNode", ...] = ()
    view: str | None = None
    props: dict[str, object] = field(default_factory=dict)


CONTAINER_KINDS = {
    "tabs",
    "row",
    "column",
    "grid",
    "stack",
    "panel",
    "collapsible_panel",
    "split_pane",
    "sidebar",
    "control_group",
    "view_switcher",
}


def view_slot(view_name: str, **props: object) -> LayoutNode:
    return LayoutNode(kind="view_slot", view=view_name, props=dict(props))


def container(kind: str, children: Iterable[LayoutNode], **props: object) -> LayoutNode:
    if kind not in CONTAINER_KINDS:
        raise InvalidLayoutError(f"Unsupported layout kind: {kind}")
    return LayoutNode(kind=kind, children=tuple(children), props=dict(props))


def validate_layout(layout: LayoutNode, known_views: set[str]) -> None:
    if layout.kind == "view_slot":
        if not layout.view or layout.view not in known_views:
            raise InvalidLayoutError(f"Unknown view slot: {layout.view!r}")
        return
    if layout.kind not in CONTAINER_KINDS:
        raise InvalidLayoutError(f"Unsupported layout kind: {layout.kind}")
    if not layout.children:
        raise InvalidLayoutError(f"Container '{layout.kind}' must have children")
    for child in layout.children:
        validate_layout(child, known_views)
