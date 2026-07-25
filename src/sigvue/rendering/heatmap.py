"""Resolution-bounded, aggregation-aware Plotly heatmap rendering."""

from __future__ import annotations

import base64
import warnings
from io import BytesIO
from math import ceil
from typing import Any, Literal

import numpy as np
import plotly.colors as plotly_colors
import plotly.graph_objects as go
from PIL import Image


HeatmapAggregation = Literal["max", "mean", "median"]
HEATMAP_AGGREGATIONS: tuple[HeatmapAggregation, ...] = ("max", "mean", "median")
_LUT_SIZE = 256


def aggregate_heatmap(
    values: Any,
    *,
    width: int,
    height: int,
    method: HeatmapAggregation = "mean",
) -> np.ndarray:
    """Reduce a 2-D matrix into at most ``width`` by ``height`` exact blocks."""
    matrix = np.asarray(values)
    if matrix.ndim != 2:
        raise ValueError(f"Heatmap values must be 2-D, received shape {matrix.shape}")
    if width <= 0 or height <= 0:
        raise ValueError("Heatmap render width and height must be positive")
    if method not in HEATMAP_AGGREGATIONS:
        raise ValueError(f"Unsupported heatmap aggregation: {method}")
    rows, columns = matrix.shape
    if rows == 0 or columns == 0:
        raise ValueError("Heatmap values cannot be empty")
    row_block = max(1, ceil(rows / height))
    column_block = max(1, ceil(columns / width))
    output_rows = ceil(rows / row_block)
    output_columns = ceil(columns / column_block)
    padded = np.full(
        (output_rows * row_block, output_columns * column_block),
        np.nan,
        dtype=np.result_type(matrix.dtype, np.float32),
    )
    padded[:rows, :columns] = matrix
    blocks = padded.reshape(output_rows, row_block, output_columns, column_block)
    reducer = {"max": np.nanmax, "mean": np.nanmean, "median": np.nanmedian}[method]
    with warnings.catch_warnings(), np.errstate(invalid="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.asarray(reducer(blocks, axis=(1, 3)))


def _colorscale_lut(colorscale: Any) -> np.ndarray:
    if not isinstance(colorscale, str):
        colorscale = [list(stop) for stop in colorscale]
    colors = plotly_colors.sample_colorscale(
        colorscale,
        np.linspace(0.0, 1.0, _LUT_SIZE),
        colortype="rgb",
    )
    return np.asarray([plotly_colors.unlabel_rgb(color) for color in colors], dtype=np.uint8)


def _png_uri(values: np.ndarray, *, zmin: float, zmax: float, colorscale: Any) -> str:
    zmin = float(zmin)
    zmax = float(zmax)
    if not np.isfinite(zmin) or not np.isfinite(zmax) or zmax <= zmin:
        raise ValueError(f"Heatmap requires finite zmax > zmin, received {zmin}, {zmax}")
    finite = np.isfinite(values)
    scaled = np.zeros(values.shape, dtype=np.float32)
    np.subtract(values, zmin, out=scaled, where=finite)
    scaled *= (_LUT_SIZE - 1) / (zmax - zmin)
    np.clip(scaled, 0.0, _LUT_SIZE - 1, out=scaled)
    indices = np.rint(scaled).astype(np.uint8)
    rgba = np.empty((*values.shape, 4), dtype=np.uint8)
    rgba[..., :3] = _colorscale_lut(colorscale)[indices]
    rgba[..., 3] = np.where(finite, 255, 0).astype(np.uint8)
    rgba = np.ascontiguousarray(np.flipud(rgba))
    buffer = BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(
        buffer,
        format="PNG",
        compress_level=1,
        optimize=False,
    )
    return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"


def _coordinate_bounds(values: Any, cell_count: int) -> tuple[float, float]:
    if values is None:
        return -0.5, float(cell_count) - 0.5
    coordinates = np.asarray(values, dtype=float)
    if coordinates.ndim != 1 or coordinates.size == 0:
        raise ValueError("Rasterized heatmap coordinates must be one-dimensional and non-empty")
    if coordinates.size == cell_count + 1:
        return float(coordinates[0]), float(coordinates[-1])
    if coordinates.size == 1:
        return float(coordinates[0] - 0.5), float(coordinates[0] + 0.5)
    spacing = np.diff(coordinates)
    return float(coordinates[0] - spacing[0] / 2), float(coordinates[-1] + spacing[-1] / 2)


def _axis_name(reference: str) -> str:
    return f"{reference[0]}axis{reference[1:]}"


def _coordinate_edges(values: np.ndarray | None, cell_count: int) -> np.ndarray:
    if values is None:
        return np.arange(cell_count + 1, dtype=float) - 0.5
    coordinates = np.asarray(values, dtype=float)
    if coordinates.size == cell_count + 1:
        return coordinates
    if coordinates.size == 1:
        return np.asarray([coordinates[0] - 0.5, coordinates[0] + 0.5])
    edges = np.empty(cell_count + 1, dtype=float)
    edges[1:-1] = (coordinates[:-1] + coordinates[1:]) / 2
    edges[0] = coordinates[0] - (coordinates[1] - coordinates[0]) / 2
    edges[-1] = coordinates[-1] + (coordinates[-1] - coordinates[-2]) / 2
    return edges


def _visible_slice(edges: np.ndarray, requested: Any) -> slice:
    if not isinstance(requested, (list, tuple)) or len(requested) < 2:
        return slice(0, edges.size - 1)
    try:
        low, high = sorted((float(requested[0]), float(requested[1])))
    except (TypeError, ValueError):
        return slice(0, edges.size - 1)
    if not np.isfinite(low) or not np.isfinite(high):
        return slice(0, edges.size - 1)
    cell_low = np.minimum(edges[:-1], edges[1:])
    cell_high = np.maximum(edges[:-1], edges[1:])
    # Touching an outer boundary is not visibility; require positive overlap so
    # edge-coordinate heatmaps do not acquire an extra row or column.
    indexes = np.flatnonzero((cell_high > low) & (cell_low < high))
    if not indexes.size:
        # A stale viewport must never turn into a full-source raster displayed
        # beneath unrelated zoomed axes.  Keep the result local to the nearest
        # source cell; normally viewport translation prevents this path.
        distance = np.maximum(cell_low - high, low - cell_high)
        nearest = int(np.argmin(distance))
        return slice(nearest, nearest + 1)
    return slice(int(indexes[0]), int(indexes[-1]) + 1)


def _visible_coordinates(values: np.ndarray | None, cell_count: int, selected: slice) -> np.ndarray | None:
    if values is None:
        return None
    if values.size == cell_count:
        return values[selected]
    if values.size == cell_count + 1:
        return values[selected.start:selected.stop + 1]
    raise ValueError("Heatmap coordinates must contain one center per cell or one more edge than cells")


def _aggregated_coordinate_edges(
    edges: np.ndarray,
    selected: slice,
    block_size: int,
    output_count: int,
) -> np.ndarray:
    """Return the exact source boundaries represented by reduced display cells."""
    source_count = selected.stop - selected.start
    offsets = np.minimum(
        np.arange(output_count + 1, dtype=int) * block_size,
        source_count,
    )
    offsets[-1] = source_count
    return edges[selected.start + offsets]


def _uniform_cell_widths(edges: np.ndarray) -> bool:
    widths = np.diff(edges)
    if widths.size < 2:
        return True
    scale = max(1.0, float(np.max(np.abs(widths))))
    return bool(np.allclose(widths, widths[0], rtol=1e-10, atol=scale * 1e-12))


def _translated_viewport(requested: Any, current: tuple[float, float]) -> Any:
    """Translate a requested range onto the current source bounds."""
    if not isinstance(requested, dict):
        return requested
    visible = requested.get("range")
    previous = requested.get("base")
    if not all(isinstance(value, (list, tuple)) and len(value) >= 2 for value in (visible, previous, current)):
        return visible
    try:
        visible_low, visible_high = map(float, visible[:2])
        previous_low, previous_high = map(float, previous[:2])
        current_low, current_high = map(float, current[:2])
    except (TypeError, ValueError):
        return visible
    values = np.asarray(
        [visible_low, visible_high, previous_low, previous_high, current_low, current_high],
        dtype=float,
    )
    previous_width = previous_high - previous_low
    if not np.all(np.isfinite(values)) or abs(previous_width) <= np.finfo(float).eps:
        return visible
    current_width = current_high - current_low
    center_fraction = ((visible_low + visible_high) / 2 - previous_low) / previous_width
    center = current_low + center_fraction * current_width
    half_span = abs(visible_high - visible_low) / 2
    direction = 1.0 if visible_high >= visible_low else -1.0
    translated = [center - half_span, center + half_span]
    allowed = sorted((current_low, current_high))
    span = translated[1] - translated[0]
    if span >= allowed[1] - allowed[0]:
        translated = allowed
    else:
        if translated[0] < allowed[0]:
            translated[1] += allowed[0] - translated[0]
            translated[0] = allowed[0]
        if translated[1] > allowed[1]:
            translated[0] -= translated[1] - allowed[1]
            translated[1] = allowed[1]
    return translated if direction > 0 else translated[::-1]


def _axis_viewport(figure: Any, viewport: dict[str, Any], axis_name: str) -> Any:
    """Find a requested range on an axis or any Plotly-matched partner."""
    if axis_name in viewport:
        return viewport[axis_name]
    target = axis_name
    seen: set[str] = set()
    while target not in seen:
        seen.add(target)
        axis = getattr(figure.layout, target, None)
        match = getattr(axis, "matches", None)
        if not isinstance(match, str) or not match:
            break
        target = _axis_name(match)
        if target in viewport:
            return viewport[target]
    for candidate in figure.layout:
        if not isinstance(candidate, str) or not candidate.startswith(axis_name[0] + "axis"):
            continue
        candidate_axis = getattr(figure.layout, candidate, None)
        match = getattr(candidate_axis, "matches", None)
        if isinstance(match, str) and _axis_name(match) in seen and candidate in viewport:
            return viewport[candidate]
    return None


def add_viewport_heatmap(
    figure: go.Figure,
    *,
    viewport: dict[str, Any] | None = None,
    render_width: int = 1024,
    render_height: int = 512,
    aggregation: HeatmapAggregation = "mean",
    row: int | None = None,
    col: int | None = None,
    **heatmap: Any,
) -> int:
    """Render exactly the visible source region, using native data when it fits."""
    if render_width <= 0 or render_height <= 0:
        raise ValueError("Heatmap render width and height must be positive")
    if aggregation not in HEATMAP_AGGREGATIONS:
        raise ValueError(f"Unsupported heatmap aggregation: {aggregation}")
    trace = go.Heatmap(**heatmap)
    source = np.asarray(trace.z)
    if source.ndim != 2 or not source.size:
        raise ValueError("Heatmap values must be a non-empty two-dimensional matrix")
    x_values = np.asarray(trace.x, dtype=float) if trace.x is not None else None
    y_values = np.asarray(trace.y, dtype=float) if trace.y is not None else None
    x_edges = _coordinate_edges(x_values, source.shape[1])
    y_edges = _coordinate_edges(y_values, source.shape[0])
    if row is None and col is None:
        figure.add_trace(trace)
    elif row is not None and col is not None:
        figure.add_trace(trace, row=row, col=col)
    else:
        raise ValueError("Heatmap subplot row and col must be supplied together")
    trace_index = len(figure.data) - 1
    attached = figure.data[trace_index]
    xref, yref = attached.xaxis or "x", attached.yaxis or "y"
    requested = viewport if isinstance(viewport, dict) else {}
    x_request = _axis_viewport(figure, requested, _axis_name(xref))
    y_request = _axis_viewport(figure, requested, _axis_name(yref))
    x_bounds = (float(x_edges[0]), float(x_edges[-1]))
    y_bounds = (float(y_edges[0]), float(y_edges[-1]))
    x_slice = _visible_slice(x_edges, _translated_viewport(x_request, x_bounds))
    y_slice = _visible_slice(y_edges, _translated_viewport(y_request, y_bounds))
    visible = source[y_slice, x_slice]
    visible_x = _visible_coordinates(x_values, source.shape[1], x_slice)
    visible_y = _visible_coordinates(y_values, source.shape[0], y_slice)
    attached.x = visible_x
    attached.y = visible_y
    attached.z = visible
    figure._sigvue_viewport_heatmap = True
    if visible.shape[0] <= render_height and visible.shape[1] <= render_width:
        return trace_index

    finite = source[np.isfinite(source)]
    if finite.size == 0:
        raise ValueError("Heatmap values must contain at least one finite value")
    zmin = float(trace.zmin) if trace.zmin is not None else float(np.min(finite))
    zmax = float(trace.zmax) if trace.zmax is not None else float(np.max(finite))
    if zmax <= zmin:
        zmax = zmin + max(1.0, abs(zmin) * 1e-9)
    rendered = aggregate_heatmap(visible, width=render_width, height=render_height, method=aggregation)
    row_block = max(1, ceil(visible.shape[0] / render_height))
    column_block = max(1, ceil(visible.shape[1] / render_width))
    rendered_x_edges = _aggregated_coordinate_edges(
        x_edges,
        x_slice,
        column_block,
        rendered.shape[1],
    )
    rendered_y_edges = _aggregated_coordinate_edges(
        y_edges,
        y_slice,
        row_block,
        rendered.shape[0],
    )
    if not (
        _uniform_cell_widths(rendered_x_edges)
        and _uniform_cell_widths(rendered_y_edges)
    ):
        # A single layout image necessarily gives every pixel the same size.
        # Keep the bounded Heatmap trace when reduced cells have unequal
        # physical extents so partial edge blocks are drawn at their exact
        # coordinates instead of being stretched across a full raster row.
        attached.x = rendered_x_edges
        attached.y = rendered_y_edges
        attached.z = rendered
        attached.zsmooth = False
        return trace_index
    xmin, xmax = sorted((float(x_edges[x_slice.start]), float(x_edges[x_slice.stop])))
    ymin, ymax = sorted((float(y_edges[y_slice.start]), float(y_edges[y_slice.stop])))
    figure.add_layout_image(
        source=_png_uri(rendered, zmin=zmin, zmax=zmax, colorscale=trace.colorscale),
        name=trace.name, xref=xref, yref=yref, x=xmin, y=ymax,
        sizex=xmax - xmin, sizey=ymax - ymin, xanchor="left", yanchor="top",
        sizing="stretch", opacity=1.0, layer="below",
        visible=trace.visible is not False and trace.visible != "legendonly",
    )
    # Keep the bounded values behind the raster so Plotly can still report
    # meaningful x/y/z hover data.  The trace is transparent, while the layout
    # image remains the only visible rendering.
    attached.x = rendered_x_edges
    attached.y = rendered_y_edges
    attached.z = rendered
    attached.opacity = 0.0
    return trace_index


__all__ = [
    "HEATMAP_AGGREGATIONS",
    "HeatmapAggregation",
    "add_viewport_heatmap",
    "aggregate_heatmap",
]
