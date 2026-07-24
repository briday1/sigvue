"""Small Plotly additions that are independent of workspace layout."""

from __future__ import annotations

from collections.abc import Iterable
from html import escape
from math import isfinite

import plotly.graph_objects as go

from sigvue.plugin import Annotation


def add_time_frequency_annotation_regions(
    figure: go.Figure,
    annotations: Iterable[Annotation],
    *,
    time_range: tuple[float, float],
    frequency_range: tuple[float, float],
    seconds_to_axis: float = 1.0,
    hertz_to_axis: float = 1.0,
    time_unit: str = "s",
    frequency_unit: str = "Hz",
    color: str = "#ffffff",
    width: float = 1.5,
    opacity: float = 0.8,
    row: int | None = None,
    col: int | None = None,
) -> go.Figure:
    """Add exact, hoverable annotation outlines to a time-frequency plot.

    Annotation coordinates remain separate vector traces; heatmap rasterization
    therefore cannot translate or aggregate their boundaries.
    """
    if (
        len(time_range) != 2
        or len(frequency_range) != 2
        or not all(isfinite(value) for value in (*time_range, *frequency_range))
        or time_range[0] >= time_range[1]
        or frequency_range[0] >= frequency_range[1]
    ):
        raise ValueError("Annotation plot ranges must be finite and increasing")
    if (
        not isfinite(seconds_to_axis)
        or seconds_to_axis <= 0
        or not isfinite(hertz_to_axis)
        or hertz_to_axis <= 0
    ):
        raise ValueError("Annotation axis scales must be finite and positive")
    if not isfinite(width) or width <= 0:
        raise ValueError("Annotation line width must be positive")
    if not isfinite(opacity) or not 0 <= opacity <= 1:
        raise ValueError("Annotation opacity must be between zero and one")

    line_x: list[float | None] = []
    line_y: list[float | None] = []
    hover_x: list[float] = []
    hover_y: list[float] = []
    hover_text: list[str] = []
    time_lower, time_upper = time_range
    frequency_lower, frequency_upper = frequency_range

    for annotation in annotations:
        start = annotation.start_seconds * seconds_to_axis
        stop = (
            annotation.start_seconds
            + (annotation.duration_seconds or 0.0)
        ) * seconds_to_axis
        lower = (
            annotation.frequency_lower_hz * hertz_to_axis
            if annotation.frequency_lower_hz is not None
            else frequency_lower
        )
        upper = (
            annotation.frequency_upper_hz * hertz_to_axis
            if annotation.frequency_upper_hz is not None
            else frequency_upper
        )
        if (
            stop < time_lower
            or start > time_upper
            or upper < frequency_lower
            or lower > frequency_upper
        ):
            continue

        if stop > start:
            line_x.extend((lower, upper, upper, lower, lower, None))
            line_y.extend((start, start, stop, stop, start, None))
        else:
            line_x.extend((lower, upper, None))
            line_y.extend((start, start, None))
        hover_x.append(
            (
                max(lower, frequency_lower)
                + min(upper, frequency_upper)
            )
            / 2
        )
        hover_y.append(
            (max(start, time_lower) + min(stop, time_upper)) / 2
        )
        frequency = (
            f"{lower:.9g}–{upper:.9g} {escape(frequency_unit)}"
            if (
                annotation.frequency_lower_hz is not None
                and annotation.frequency_upper_hz is not None
            )
            else "Full displayed frequency span"
        )
        details = [
            f"<b>{escape(annotation.label or 'Annotation')}</b>",
            f"Time: {start:.9g}–{stop:.9g} {escape(time_unit)}",
            f"Duration: {stop - start:.9g} {escape(time_unit)}",
            f"Frequency: {frequency}",
        ]
        if annotation.comment:
            details.append(escape(annotation.comment))
        hover_text.append("<br>".join(details))

    if not line_x:
        return figure

    figure.add_trace(
        go.Scatter(
            x=line_x,
            y=line_y,
            mode="lines",
            line={"color": color, "width": width},
            opacity=opacity,
            name="Annotations",
            showlegend=False,
            hoverinfo="skip",
        ),
        row=row,
        col=col,
    )
    figure.add_trace(
        go.Scatter(
            x=hover_x,
            y=hover_y,
            mode="markers",
            marker={
                "color": color,
                "size": max(8.0, width * 4),
                "opacity": max(0.15, min(0.45, opacity)),
                "symbol": "square-open",
            },
            text=hover_text,
            hovertemplate="%{text}<extra></extra>",
            name="Annotation details",
            showlegend=False,
        ),
        row=row,
        col=col,
    )
    return figure
