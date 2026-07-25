"""Headless-first synthetic LTE waterfall pipeline."""

from .analysis import analyze
from .models import WaterfallProducts, WaterfallSettings
from .plots import plot, plot_waterfall


def create_workspace(config):
    from .sigvue import create_workspace as build

    return build(config)


__all__ = [
    "WaterfallProducts",
    "WaterfallSettings",
    "analyze",
    "create_workspace",
    "plot",
    "plot_waterfall",
]
