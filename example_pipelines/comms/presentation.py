"""UI presentation for communications analysis products."""

from sigvue.plugin import Presentation, ViewContext

from ..style import style_figure
from ..memory import format_bytes
from .models import CommsProducts
from .plots import constellation_figure, eye_figure


def present(products: CommsProducts, ui: ViewContext) -> None:
    """Lay out statistics and plots in the workspace UI."""
    ui.stat("Modulation", products.modulation)
    ui.stat("Samples per symbol", products.samples_per_symbol)
    ui.stat("Recovered symbols", products.symbols.size)
    ui.stat("Window start", f"{products.start_seconds * 1e3:.3f} ms")
    ui.stat("Window width", f"{products.duration_seconds * 1e3:.3f} ms")
    ui.stat("Buffer memory", format_bytes(products.buffer_nbytes))
    with ui.tab("Constellation"):
        ui.plot(
            style_figure(
                constellation_figure(products), ui.theme,
                f"{products.modulation} constellation",
            ),
            key="constellation",
            axis_navigation="bounded",
        )
    with ui.tab("Eye diagram"):
        ui.plot(
            style_figure(
                eye_figure(products), ui.theme,
                f"{products.modulation} eye diagram",
            ),
            key="eye",
            axis_navigation="bounded",
        )


class CommsPresentation(Presentation[CommsProducts]):
    """Framework presentation object for constellation and eye views."""

    def present(self, products: CommsProducts, ui: ViewContext) -> None:
        present(products, ui)
