"""Pure Plotly figure builders for analyzed waterfall products."""

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from sigvue.plugin import add_viewport_heatmap

from ..plugins import add_time_frequency_annotation_regions
from .models import WaterfallProducts


def waterfall_figure(
    products: WaterfallProducts,
    *,
    viewport: object,
    colormap: str,
    zmin: float,
    zmax: float,
    spectrum_ymin: float,
    spectrum_ymax: float,
    spectrum_style: object,
    show_colorbar: bool,
    render_width: int,
    render_height: int,
    aggregation: str,
    annotations: tuple[object, ...] = (),
    annotation_color: str = "#ffffff",
    annotation_width: float = 1.5,
    annotation_opacity: float = 0.8,
) -> go.Figure:
    """Build the spectrum/waterfall figure from explicit display settings."""
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
        mode=spectrum_style.mode,
        line=spectrum_style.line,
        marker=spectrum_style.plotly_marker,
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
