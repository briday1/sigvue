"""Quiet Plotly defaults shared by the copyable examples."""

from typing import Any


COLORS = ("#087e8b", "#d35d35", "#7656a5", "#5b7f3b")
INK = "#13212b"
MUTED = "#60717d"
GRID = "#dce5e8"
TEAL = COLORS[0]
ORANGE = COLORS[1]


def heatmap_grid_color(theme: str) -> str:
    """Return a quiet grid that remains legible without competing with heatmap data."""
    return "rgba(169,189,194,0.13)" if theme == "dark" else "rgba(96,113,125,0.12)"


def style_figure(figure: Any, theme: str, title: str) -> Any:
    dark = theme == "dark"
    grid = "#36515b" if dark else GRID
    figure.update_layout(
        template="plotly_dark" if dark else "simple_white",
        paper_bgcolor="#10252d" if dark else "white",
        plot_bgcolor="#10252d" if dark else "white",
        font={"family": "system-ui, -apple-system, sans-serif", "color": "#e7f1f3" if dark else INK, "size": 12},
        title={"text": title, "x": 0.01, "y": 0.98, "xanchor": "left", "yanchor": "top", "font": {"size": 15}},
        margin={"l": 70, "r": 30, "t": 68, "b": 56},
        legend={
            "orientation": "h",
            "x": 0.99,
            "y": 0.98,
            "xanchor": "right",
            "yanchor": "top",
            "bgcolor": "rgba(16,37,45,0.72)" if dark else "rgba(255,255,255,0.82)",
        },
    )
    muted = "#a9bdc2" if dark else MUTED
    figure.update_xaxes(showgrid=True, gridcolor=grid, gridwidth=0.5, showline=True, mirror=True, linecolor=grid, zeroline=False, ticks="outside", tickcolor=muted)
    figure.update_yaxes(showgrid=True, gridcolor=grid, gridwidth=0.5, showline=True, mirror=True, linecolor=grid, zeroline=False, ticks="outside", tickcolor=muted)
    return figure
