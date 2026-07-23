"""UI presentation for waterfall analysis products."""

from sigvue.plugin import Presentation, ViewContext

from ..style import TEAL, heatmap_grid_color, style_figure
from ..memory import format_bytes
from .models import WaterfallProducts
from .plots import waterfall_figure
from .scales import automatic_dbfs_ranges


COLORMAPS = ("Plasma", "Viridis", "Cividis", "Inferno", "Magma", "Turbo")


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
    ui.stat("Center frequency", f"{products.recording.center_frequency / 1e6:g} MHz")
    ui.stat("Buffer memory", format_bytes(products.buffer_nbytes))
    with ui.tab("Spectrum + waterfall"):
        ui.plot(figure, key="lte-waterfall", axis_navigation="bounded")


class WaterfallPresentation(Presentation[WaterfallProducts]):
    """Framework presentation object for the waterfall views."""

    def present(self, products: WaterfallProducts, ui: ViewContext) -> None:
        present(products, ui)
