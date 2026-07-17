"""Whole-recording entry point for the shared LFM analysis pipeline."""
from pathlib import Path

from .lfm_pipeline import WholeFileDelivery, create_lfm_workspace


def create_workspace(
    path: Path | None = None,
    *,
    identifier: str = "lfm-full-recording",
    name: str = "10 MHz LFM Collection (Whole Recording)",
):
    return create_lfm_workspace(
        path,
        identifier=identifier,
        name=name,
        delivery=WholeFileDelivery(),
    )
