from __future__ import annotations

import base64
import io


def render_matplotlib_figure(figure: object, *, dpi: int = 120, format: str = "png") -> str:
    """Encode a native Matplotlib figure for transport to the browser.

    The figure is deliberately left intact: static views can be cached by the
    workspace and rendered again after a browser refresh.
    """
    buffer = io.BytesIO()
    savefig = getattr(figure, "savefig")
    savefig(buffer, format=format, dpi=dpi, bbox_inches="tight")
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode("ascii")
