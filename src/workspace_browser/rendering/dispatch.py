from __future__ import annotations

from enum import StrEnum


class RenderKind(StrEnum):
    PLOTLY = "plotly"
    MATPLOTLIB = "matplotlib"
    DATAFRAME = "dataframe"
    TABLE = "table"
    IMAGE = "image"
    MARKDOWN = "markdown"
    TEXT = "text"
    DOWNLOAD = "download"
    UNKNOWN = "unknown"


def detect_render_kind(value: object) -> RenderKind:
    if value is None:
        return RenderKind.TEXT
    if isinstance(value, dict) and value.get("type") in {"plotly", "plot_grid"}:
        return RenderKind.PLOTLY
    module = type(value).__module__.lower()
    name = type(value).__name__.lower()

    if "plotly" in module:
        return RenderKind.PLOTLY
    if "matplotlib" in module or name.startswith("figure"):
        return RenderKind.MATPLOTLIB
    if "pandas" in module and name == "dataframe":
        return RenderKind.DATAFRAME
    if isinstance(value, (list, tuple)) and (not value or isinstance(value[0], dict)):
        return RenderKind.TABLE
    if isinstance(value, dict) and "download_path" in value:
        return RenderKind.DOWNLOAD
    if isinstance(value, dict) and value.get("type") == "image":
        return RenderKind.IMAGE
    if isinstance(value, str):
        stripped = value.lstrip()
        is_markdown_heading = stripped.startswith("# ")
        is_markdown_multiline = "\n# " in stripped or stripped.startswith("## ") or stripped.startswith("### ")
        return RenderKind.MARKDOWN if is_markdown_heading or is_markdown_multiline else RenderKind.TEXT
    return RenderKind.UNKNOWN
