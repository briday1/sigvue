"""Headless-first synthetic digital communications pipeline."""

from .analysis import analyze
from .models import CommsProducts
from .plots import plot, plot_constellation, plot_eye


def create_workspace(config):
    from .sigvue import create_workspace as build

    return build(config)


__all__ = [
    "CommsProducts",
    "analyze",
    "create_workspace",
    "plot",
    "plot_constellation",
    "plot_eye",
]
