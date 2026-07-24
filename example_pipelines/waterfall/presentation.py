"""UI presentation for waterfall analysis products."""

import numpy as np

from sigvue.helpers import format_bytes
from sigvue.plugin import ViewContext

from ..plugins.sigmf import read_sigmf_annotations
from ..style import TEAL, heatmap_grid_color, style_figure
from .models import WaterfallProducts
from .plots import waterfall_figure


COLORMAPS = ("Plasma", "Viridis", "Cividis", "Inferno", "Magma", "Turbo")


def automatic_dbfs_ranges(
    products: WaterfallProducts,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return robust waterfall and average-spectrum ranges, rounded to 5 dB."""
    waterfall = _finite(products.waterfall_dbfs)
    spectrum = _finite(products.spectrum_dbfs)
    if not waterfall.size or not spectrum.size:
        return (-90.0, -20.0), (-90.0, -20.0)

    # The upper waterfall tail preserves narrow carriers that occupy far less
    # than one percent of the raster. The time-averaged spectrum supplies a
    # second, outlier-resistant indication of real persistent signal energy.
    signal_top = max(
        float(np.percentile(waterfall, 99.9)),
        float(np.percentile(spectrum, 99.5)),
    )
    waterfall_range = _rounded_range(
        float(np.percentile(waterfall, 10.0)) - 3.0,
        signal_top + 3.0,
    )
    spectrum_range = _rounded_range(
        float(np.percentile(spectrum, 1.0)) - 3.0,
        float(np.percentile(spectrum, 99.9)) + 3.0,
    )
    return waterfall_range, spectrum_range


def _finite(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    return array[np.isfinite(array)]


def _rounded_range(lower: float, upper: float) -> tuple[float, float]:
    lower = max(-140.0, 5.0 * np.floor(lower / 5.0))
    upper = min(0.0, 5.0 * np.ceil(upper / 5.0))
    if upper - lower < 20.0:
        lower = max(-140.0, upper - 20.0)
    return float(lower), float(upper)


def present(products: WaterfallProducts, ui: ViewContext) -> None:
    """Collect display controls and lay out the waterfall view."""
    colormap = ui.colormap(
        "colormap", label="Waterfall colormap", default="Plasma",
        options=COLORMAPS, group="Display",
    )
    automatic_waterfall, automatic_spectrum = automatic_dbfs_ranges(products)
    zmin, zmax = ui.limits(
        "dbfs_limits", label="Waterfall dBFS limits", default=automatic_waterfall,
        minimum=-140.0, maximum=0.0, step=1.0, group="Display",
    )
    spectrum_ymin, spectrum_ymax = ui.limits(
        "spectrum_dbfs_limits", label="Average power limits (dBFS)",
        default=automatic_spectrum, minimum=-140.0, maximum=0.0,
        step=1.0, group="Display",
    )
    spectrum_style = ui.trace_style(
        "spectrum_style", label="Average spectrum", color=TEAL,
        width=1.4, group="Display",
    )
    show_colorbar = ui.toggle(
        "show_colorbar", label="Show colorbar", default=True, group="Display",
    )
    show_annotations = ui.toggle(
        "show_annotations",
        label="Show annotations",
        default=True,
        group="Annotations",
    )
    annotation_color = ui.color(
        "annotation_region_color",
        label="Annotation color",
        default="#ffffff",
        group="Annotations",
    )
    annotation_width = float(ui.number(
        "annotation_region_width",
        label="Line weight",
        default=1.5,
        minimum=0.5,
        maximum=8.0,
        step=0.5,
        group="Annotations",
    ))
    annotation_opacity = float(ui.number(
        "annotation_region_opacity",
        label="Opacity",
        default=0.8,
        minimum=0.05,
        maximum=1.0,
        step=0.05,
        group="Annotations",
    ))
    with ui.details_group("Raster rendering"):
        render_width = int(ui.select(
            "render_width", label="Heatmap render width", default=1024,
            options=(256, 512, 1024, 2048),
        ))
        render_height = int(ui.select(
            "render_height", label="Heatmap render height", default=512,
            options=(128, 256, 512, 1024),
        ))
        aggregation = str(ui.select(
            "render_aggregation", label="Heatmap aggregation", default="mean",
            options=("max", "mean", "median"),
        ))
    title = str(products.recording.metadata["global"].get("core:description", "Synthetic LTE"))

    def figure():
        rendered = waterfall_figure(
            products,
            viewport=ui.plot_viewport("lte-waterfall"),
            colormap=colormap,
            zmin=zmin,
            zmax=zmax,
            spectrum_ymin=spectrum_ymin,
            spectrum_ymax=spectrum_ymax,
            spectrum_style=spectrum_style,
            show_colorbar=show_colorbar,
            render_width=render_width,
            render_height=render_height,
            aggregation=aggregation,
            annotations=(
                read_sigmf_annotations(products.recording)
                if show_annotations else ()
            ),
            annotation_color=annotation_color,
            annotation_width=annotation_width,
            annotation_opacity=annotation_opacity,
        )
        styled = style_figure(rendered, ui.theme, title)
        styled.update_xaxes(
            gridcolor=heatmap_grid_color(ui.theme), gridwidth=0.35, row=2, col=1,
        )
        styled.update_yaxes(
            gridcolor=heatmap_grid_color(ui.theme), gridwidth=0.35, row=2, col=1,
        )
        return styled

    ui.stat("Sample rate", f"{products.recording.sample_rate / 1e6:g} MS/s")
    ui.stat(
        "Center frequency",
        f"{products.recording.center_frequency_at(products.start_sample) / 1e6:g} MHz",
    )
    ui.stat("Buffer memory", format_bytes(products.buffer_nbytes))
    with ui.tab("Spectrum + waterfall"):
        ui.plot(figure, key="lte-waterfall", axis_navigation="bounded")
