"""Shared visual identity for the in-repository examples."""

from .plotly import COLORS, ORANGE, TEAL, heatmap_grid_color, style_figure
from .annotations import add_time_frequency_annotation_regions

__all__ = [
    "COLORS",
    "ORANGE",
    "TEAL",
    "add_time_frequency_annotation_regions",
    "heatmap_grid_color",
    "style_figure",
]
