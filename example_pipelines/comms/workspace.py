"""Framework assembly for the windowed communications pipeline."""

from pathlib import Path

from sigvue.plugin import DiscoveryColumn, Workspace

from .analysis import CommsAnalysis
from .delivery import WindowedCommsDelivery
from .presentation import CommsPresentation
from .source import recording_source


DISCOVERY_COLUMNS = (
    DiscoveryColumn("date", "Date", "datetime"),
    DiscoveryColumn("sample_rate", "Sampling rate", "si", unit="sample/s"),
    DiscoveryColumn("rf_frequency", "RF frequency", "si", unit="Hz"),
)


def create_workspace(config=None) -> Workspace:
    values = config or {}
    root = Path(values.get("data_root", Path.cwd() / "example_pipelines/data/comms"))
    return Workspace(
        identifier="synthetic-comms",
        name="Synthetic Communications",
        description="Windowed constellation and eye-diagram analysis for generated QPSK, 16-QAM, and 64-QAM recordings.",
        source=recording_source(root),
        delivery=WindowedCommsDelivery(),
        analysis=CommsAnalysis(),
        presentation=CommsPresentation(),
        category="digital communications",
        tags=("windowed", "synthetic", "SigMF", "QPSK", "16-QAM", "64-QAM"),
        discovery_columns=DISCOVERY_COLUMNS,
    )
