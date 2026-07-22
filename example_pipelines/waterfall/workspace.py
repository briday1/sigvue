"""The only module that assembles framework objects for this pipeline."""

from pathlib import Path

from sigvue.plugin import DiscoveryColumn, Workspace

from .analysis import WaterfallAnalysis
from .delivery import WindowedSamples
from .presentation import WaterfallPresentation
from .source import recording_source


DISCOVERY_COLUMNS = (
    DiscoveryColumn("date", "Date", "datetime"),
    DiscoveryColumn("sample_rate", "Sampling rate", "si", unit="sample/s"),
    DiscoveryColumn("rf_frequency", "RF frequency", "si", unit="Hz"),
)


def create_workspace(config=None) -> Workspace:
    values = config or {}
    root = Path(values.get("data_root", Path.cwd() / "example_pipelines/data/lte"))
    return Workspace(
        identifier="synthetic-lte-waterfall",
        name="Synthetic LTE Waterfall",
        description="Windowed spectrum and waterfall analysis of generated LTE-like uplink and downlink SigMF recordings.",
        source=recording_source(root),
        delivery=WindowedSamples(),
        analysis=WaterfallAnalysis(),
        presentation=WaterfallPresentation(),
        category="spectrum monitoring",
        tags=("windowed", "synthetic", "LTE", "SigMF", "waterfall"),
        discovery_columns=DISCOVERY_COLUMNS,
    )
