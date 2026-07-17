"""Shared presentation defaults for the bundled plotting examples."""

from __future__ import annotations

from typing import Any


INK = "#13212b"
MUTED = "#60717d"
GRID = "#dce5e8"
TEAL = "#087e8b"
ORANGE = "#d35d35"


def style_plotly(figure: Any, *, title: str | None = None) -> Any:
    """Give Plotly figures a compact, consistent scientific plotting treatment."""
    figure.update_layout(
        template="simple_white",
        paper_bgcolor="white",
        plot_bgcolor="white",
        font={"family": "system-ui, -apple-system, sans-serif", "color": INK, "size": 12},
        margin={"l": 62, "r": 28, "t": 52, "b": 54},
        hovermode="x unified",
        title={"text": title, "x": 0.02, "xanchor": "left", "font": {"size": 15}},
        legend={"orientation": "h", "x": 0, "y": 1.12, "yanchor": "bottom"},
    )
    figure.update_xaxes(showline=True, linecolor=GRID, gridcolor=GRID, zeroline=False, ticks="outside", tickcolor=MUTED)
    figure.update_yaxes(showline=True, linecolor=GRID, gridcolor=GRID, zeroline=False, ticks="outside", tickcolor=MUTED)
    return figure


def style_matplotlib(figure: Any, axes: Any, *, title: str, x_label: str, y_label: str) -> Any:
    """Match a Matplotlib figure to the same quiet axes treatment as Plotly."""
    figure.patch.set_facecolor("white")
    axes.set(title=title, xlabel=x_label, ylabel=y_label)
    axes.set_facecolor("white")
    axes.grid(True, color=GRID, linewidth=0.8, alpha=0.9)
    axes.tick_params(colors=MUTED, labelsize=9)
    axes.xaxis.label.set_color(INK)
    axes.yaxis.label.set_color(INK)
    axes.title.set_color(INK)
    axes.title.set_fontsize(11)
    axes.title.set_fontweight("normal")
    for spine in ("top", "right"):
        axes.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        axes.spines[spine].set_color(GRID)
    return figure
