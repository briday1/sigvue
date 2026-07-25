"""Pure Plotly figure builders for analyzed waterfall products."""

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from sigvue import add_viewport_heatmap

from ..style import TEAL, add_time_frequency_annotation_regions
from .models import WaterfallProducts


def automatic_dbfs_ranges(
    products: WaterfallProducts,
) -> tuple[tuple[float, float], tuple[float, float]]:
    waterfall = _finite(products.waterfall_dbfs)
    spectrum = _finite(products.spectrum_dbfs)
    if not waterfall.size or not spectrum.size:
        return (-90.0, -20.0), (-90.0, -20.0)
    signal_top = max(
        float(np.percentile(waterfall, 99.9)),
        float(np.percentile(spectrum, 99.5)),
    )
    return (
        _rounded_range(
            float(np.percentile(waterfall, 10.0)) - 3.0,
            signal_top + 3.0,
        ),
        _rounded_range(
            float(np.percentile(spectrum, 1.0)) - 3.0,
            float(np.percentile(spectrum, 99.9)) + 3.0,
        ),
    )


def _finite(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    return array[np.isfinite(array)]


def _rounded_range(lower: float, upper: float) -> tuple[float, float]:
    lower = max(-140.0, 5.0 * np.floor(lower / 5.0))
    upper = min(0.0, 5.0 * np.ceil(upper / 5.0))
    if upper - lower < 20.0:
        lower = max(-140.0, upper - 20.0)
    return float(lower), float(upper)


def waterfall_figure(
    products: WaterfallProducts,
    *,
    viewport: object = None,
    colormap: str = "Plasma",
    zmin: float | None = None,
    zmax: float | None = None,
    spectrum_ymin: float | None = None,
    spectrum_ymax: float | None = None,
    spectrum_style: object | None = None,
    show_colorbar: bool = True,
    render_width: int = 1024,
    render_height: int = 512,
    aggregation: str = "mean",
    annotations: tuple[object, ...] = (),
    annotation_color: str = "#ffffff",
    annotation_width: float = 1.5,
    annotation_opacity: float = 0.8,
) -> go.Figure:
    """Build the spectrum/waterfall figure from explicit display settings."""
    automatic_waterfall, automatic_spectrum = automatic_dbfs_ranges(products)
    if zmin is None:
        zmin = automatic_waterfall[0]
    if zmax is None:
        zmax = automatic_waterfall[1]
    if spectrum_ymin is None:
        spectrum_ymin = automatic_spectrum[0]
    if spectrum_ymax is None:
        spectrum_ymax = automatic_spectrum[1]
    spectrum_mode = getattr(spectrum_style, "mode", "lines")
    spectrum_line = getattr(
        spectrum_style,
        "line",
        {"color": TEAL, "width": 1.4},
    )
    spectrum_marker = getattr(spectrum_style, "plotly_marker", None)
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=(0.12, 0.88),
        vertical_spacing=0.04,
    )
    figure.add_trace(go.Scatter(
        x=products.frequency_mhz,
        y=products.spectrum_dbfs,
        mode=spectrum_mode,
        line=spectrum_line,
        marker=spectrum_marker,
        name="Average spectrum",
    ), row=1, col=1)
    add_viewport_heatmap(
        figure,
        viewport=viewport,
        x=products.frequency_mhz,
        y=products.time_edges_ms,
        z=products.waterfall_dbfs,
        zmin=zmin,
        zmax=zmax,
        colorscale=colormap,
        showscale=show_colorbar,
        colorbar={"title": "dBFS"},
        render_width=render_width,
        render_height=render_height,
        aggregation=aggregation,
        row=2,
        col=1,
    )
    frequency_step = (
        float(abs(products.frequency_mhz[1] - products.frequency_mhz[0]))
        if products.frequency_mhz.size > 1 else 1.0
    )
    frequency_range = (
        float(products.frequency_mhz[0] - frequency_step / 2),
        float(products.frequency_mhz[-1] + frequency_step / 2),
    )
    add_time_frequency_annotation_regions(
        figure,
        annotations,
        time_range=(
            float(products.time_edges_ms[0]),
            float(products.time_edges_ms[-1]),
        ),
        frequency_range=frequency_range,
        seconds_to_axis=1e3,
        hertz_to_axis=1e-6,
        time_unit="ms",
        frequency_unit="MHz",
        color=annotation_color,
        width=annotation_width,
        opacity=annotation_opacity,
        row=2,
        col=1,
    )
    figure.update_yaxes(
        title_text="Power (dBFS)", range=[spectrum_ymin, spectrum_ymax],
        autorange=False, row=1, col=1,
    )
    figure.update_yaxes(
        title_text="Recording time (ms)",
        range=[float(products.time_edges_ms[0]), float(products.time_edges_ms[-1])],
        autorange=False,
        row=2,
        col=1,
    )
    figure.update_xaxes(
        title_text="RF frequency (MHz)",
        range=list(frequency_range),
        autorange=False,
        row=2,
        col=1,
    )
    figure.update_layout(uirevision=f"lte-waterfall:{products.recording.metadata_path}")
    return figure


def plot_waterfall(
    products: WaterfallProducts,
    **display_options: object,
) -> go.Figure:
    return waterfall_figure(products, **display_options)


def plot(
    products: WaterfallProducts,
    **display_options: object,
) -> go.Figure:
    return plot_waterfall(products, **display_options)


__all__ = [
    "automatic_dbfs_ranges",
    "plot",
    "plot_waterfall",
    "waterfall_figure",
]
