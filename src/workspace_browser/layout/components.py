from __future__ import annotations

from workspace_browser.core.layout import LayoutNode, container


def tabs(*children: LayoutNode) -> LayoutNode:
    return container("tabs", children)


def row(*children: LayoutNode) -> LayoutNode:
    return container("row", children)


def column(*children: LayoutNode) -> LayoutNode:
    return container("column", children)


def grid(*children: LayoutNode) -> LayoutNode:
    return container("grid", children)


def stack(*children: LayoutNode) -> LayoutNode:
    return container("stack", children)


def panel(*children: LayoutNode) -> LayoutNode:
    return container("panel", children)


def collapsible_panel(*children: LayoutNode) -> LayoutNode:
    return container("collapsible_panel", children)


def split_pane(*children: LayoutNode) -> LayoutNode:
    return container("split_pane", children)


def sidebar(*children: LayoutNode) -> LayoutNode:
    return container("sidebar", children)


def control_group(*children: LayoutNode) -> LayoutNode:
    return container("control_group", children)
