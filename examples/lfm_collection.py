"""Buffered/playback entry point for the shared LFM analysis pipeline."""
from pathlib import Path

from .lfm_pipeline import BufferedDelivery, create_lfm_workspace


def create_workspace(
    path: Path | None = None,
    *,
    identifier: str = "lfm-collection",
    name: str = "10 MHz LFM Collection",
):
    return create_lfm_workspace(
        path,
        identifier=identifier,
        name=name,
        delivery=BufferedDelivery(),
    )
