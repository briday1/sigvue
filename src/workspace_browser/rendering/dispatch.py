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
    module = type(value).__module__.lower()
    name = type(value).__name__.lower()

    if "plotly" in module:
        return RenderKind.PLOTLY
    if "matplotlib" in module or name.startswith("figure"):
        return RenderKind.MATPLOTLIB
    if "pandas" in module and name == "dataframe":
        return RenderKind.DATAFRAME
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], dict):
        return RenderKind.TABLE
    if isinstance(value, dict) and "download_path" in value:
        return RenderKind.DOWNLOAD
    if isinstance(value, dict) and value.get("type") == "image":
        return RenderKind.IMAGE
    if isinstance(value, str):
        return RenderKind.MARKDOWN if value.strip().startswith("#") else RenderKind.TEXT
    return RenderKind.UNKNOWN
