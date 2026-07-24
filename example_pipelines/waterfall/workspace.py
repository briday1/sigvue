"""Framework assembly for the windowed waterfall pipeline."""

from pathlib import Path

from sigvue.helpers import configured_path
from sigvue.plugin import Workspace

from ..plugins import CallableAnalysis, CallablePresentation
from ..plugins.sigmf import (
    SIGMF_DISCOVERY_COLUMNS,
    SigMFExporter,
    WaterfallSigMFAnnotator,
    WindowedSigMFDelivery,
    sigmf_source,
)
from .analysis import configure, process
from .presentation import present


def create_workspace(config=None) -> Workspace:
    root = configured_path(
        config,
        Path.cwd() / "example_pipelines/data/lte",
    )
    return Workspace(
        identifier="synthetic-lte-waterfall",
        name="Synthetic LTE Waterfall",
        description="Windowed spectrum and waterfall analysis of generated LTE-like uplink and downlink SigMF recordings.",
        source=sigmf_source(root, tags=("sigmf", "synthetic")),
        annotator=WaterfallSigMFAnnotator(
            "lte-waterfall",
            "annotation_region_color",
        ),
        exporter=SigMFExporter(),
        delivery=WindowedSigMFDelivery(
            default_window=0.012,
            minimum_window=0.004,
            step=0.002,
            overview_bins=300,
            overview_channel=0,
            time_unit="ms",
            cache_key="lte-power",
        ),
        analysis=CallableAnalysis(process, configure),
        presentation=CallablePresentation(present),
        lazy_views=True,
        category="spectrum monitoring",
        tags=("windowed", "synthetic", "LTE", "SigMF", "waterfall"),
        discovery_columns=SIGMF_DISCOVERY_COLUMNS,
    )
