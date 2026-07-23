from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

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


def control_slot(control_name: str) -> LayoutNode:
    return LayoutNode(kind="control_slot", props={"name": control_name})


def container(kind: str, children: Iterable[LayoutNode], **props: object) -> LayoutNode:
    if kind not in CONTAINER_KINDS:
        raise InvalidLayoutError(f"Unsupported layout kind: {kind}")
    return LayoutNode(kind=kind, children=tuple(children), props=dict(props))


def validate_layout(layout: LayoutNode, known_views: set[str], known_controls: set[str] | None = None) -> None:
    if layout.kind == "view_slot":
        if not layout.view or layout.view not in known_views:
            raise InvalidLayoutError(f"Unknown view slot: {layout.view!r}")
        return
    if layout.kind == "control_slot":
        control_name = layout.props.get("name")
        if not control_name or control_name not in (known_controls or set()):
            raise InvalidLayoutError(f"Unknown control slot: {control_name!r}")
        return
    if layout.kind not in CONTAINER_KINDS:
        raise InvalidLayoutError(f"Unsupported layout kind: {layout.kind}")
    if not layout.children:
        raise InvalidLayoutError(f"Container '{layout.kind}' must have children")
    for child in layout.children:
        validate_layout(child, known_views, known_controls)


def selected_view_names(
    layout: LayoutNode,
    values: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    """Return only the view slots visible for the requested tab/switcher state."""
    selections = values or {}
    selected: list[str] = []

    def index_for(key: str, count: int) -> int:
        try:
            index = int(selections.get(f"__view_selection_{key}", 0))
        except (TypeError, ValueError):
            index = 0
        return index if 0 <= index < count else 0

    def walk(node: LayoutNode) -> None:
        if node.kind == "view_slot":
            if node.view is not None:
                selected.append(node.view)
            return
        if node.kind == "tabs":
            key = str(node.props.get("selection_key", "__tabs"))
            walk(node.children[index_for(key, len(node.children))])
            return
        if node.kind == "view_switcher":
            raw_keys = node.props.get("selection_keys")
            keys = (
                tuple(str(key) for key in raw_keys)
                if isinstance(raw_keys, (tuple, list))
                else (str(node.props.get("key", "view")),)
            )
            coordinates = node.props.get("coordinates")
            if not isinstance(coordinates, (tuple, list)):
                coordinates = tuple((index,) for index in range(len(node.children)))
            requested = tuple(index_for(key, 1_000_000) for key in keys)
            child_index = next(
                (
                    index
                    for index, coordinate in enumerate(coordinates)
                    if tuple(int(value) for value in coordinate) == requested
                ),
                0,
            )
            walk(node.children[child_index])
            return
        for child in node.children:
            walk(child)

    walk(layout)
    return tuple(dict.fromkeys(selected))
