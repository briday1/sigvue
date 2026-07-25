"""Communications workspace built from one reader and one domain view."""

from __future__ import annotations

from dataclasses import replace
from functools import partial
from pathlib import Path

from sigvue import DataResource, UI, Workspace
from sigvue.helpers import WorkspaceConfig, format_bytes

from ..capabilities.sigmf import SigMFAnnotator, SigMFExporter
from ..formats.sigmf import (
    SigMFRecording,
    SigMFWindow,
    power_overview,
)
from ..readers import (
    SIGMF_DISCOVERY_COLUMNS,
    describe_sigmf_recording,
    sigmf_reader,
)
from ..style import style_figure
from .analysis import analyze
from .plots import constellation_figure, eye_figure


def _describe(root: Path, path: Path) -> DataResource:
    resource = describe_sigmf_recording(path, tags=("sigmf", "synthetic"))
    relative = path.resolve().relative_to(root)
    parent = relative.parent
    return replace(
        resource,
        identifier=relative.as_posix().removesuffix(".sigmf-meta").replace("/", "::"),
        navigation_path=() if parent == Path(".") else parent.parts,
    )


def _read_interval(
    recording: SigMFRecording,
    start_seconds: float,
    stop_seconds: float,
) -> SigMFWindow:
    start_sample = round(start_seconds * recording.sample_rate)
    sample_count = max(
        1,
        round((stop_seconds - start_seconds) * recording.sample_rate),
    )
    return SigMFWindow(
        recording,
        start_sample,
        recording.read(start_sample, sample_count),
    )


def create_reader(config):
    values = WorkspaceConfig(config)
    root = values.path("data_root").expanduser().resolve()
    files = sigmf_reader(
        root,
        pattern=values.string("filename", "*.sigmf-meta"),
        describe=partial(_describe, root),
        recursive=True,
    )
    return files.windowed(
        _read_interval,
        duration=lambda recording: recording.duration_seconds,
        default=0.012,
        minimum=0.002,
        step=0.001,
        overview=partial(power_overview, bins=240, channel=0),
        overview_label="Mean received power (dBFS)",
        time_unit="ms",
    )


def view(data: SigMFWindow, ui: UI) -> None:
    """Analyze one exact buffer and declare the complete communications view."""
    products = ui.compute("comms-analysis", lambda: analyze(data))

    ui.stat("Modulation", products.modulation)
    ui.stat("Samples per symbol", products.samples_per_symbol)
    ui.stat("Recovered symbols", products.symbols.size)
    ui.stat("Window start", f"{products.start_seconds * 1e3:.3f} ms")
    ui.stat("Window width", f"{products.duration_seconds * 1e3:.3f} ms")
    ui.stat("Buffer memory", format_bytes(products.buffer_nbytes))
    with ui.tab("Constellation"):
        ui.plot(
            lambda: style_figure(
                constellation_figure(products),
                ui.theme,
                f"{products.modulation} constellation",
            ),
            key="constellation",
            axis_navigation="bounded",
        )
    with ui.tab("Eye diagram"):
        ui.plot(
            lambda: style_figure(
                eye_figure(products),
                ui.theme,
                f"{products.modulation} eye diagram",
            ),
            key="eye",
            axis_navigation="bounded",
        )


def create_workspace(config) -> Workspace:
    return Workspace(
        identifier="synthetic-comms",
        name="Synthetic Communications",
        description="Windowed constellation and eye-diagram analysis for generated QPSK, 16-QAM, and 64-QAM recordings.",
        reader=create_reader(config),
        view=view,
        annotator=SigMFAnnotator(),
        exporter=SigMFExporter(),
        category="digital communications",
        tags=("windowed", "synthetic", "SigMF", "QPSK", "16-QAM", "64-QAM"),
        discovery_columns=SIGMF_DISCOVERY_COLUMNS,
    )


__all__ = ["create_reader", "create_workspace", "view"]
