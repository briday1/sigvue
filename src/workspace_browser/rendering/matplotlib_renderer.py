from __future__ import annotations

import base64
import io


def render_matplotlib_figure(figure: object, *, dpi: int = 120, format: str = "png") -> str:
    buffer = io.BytesIO()
    savefig = getattr(figure, "savefig")
    savefig(buffer, format=format, dpi=dpi)
    buffer.seek(0)
    payload = base64.b64encode(buffer.read()).decode("ascii")
    close = getattr(getattr(figure, "clf", None), "__call__", None)
    if close:
        figure.clf()
    return payload
