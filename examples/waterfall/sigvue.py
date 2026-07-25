"""Waterfall workspace built from one reader and one domain view."""

from __future__ import annotations

from dataclasses import replace
from functools import partial
from pathlib import Path

from sigvue import DataResource, UI, Workspace
from sigvue.helpers import WorkspaceConfig, format_bytes

from ..capabilities.sigmf import (
    SigMFExporter,
    WaterfallSigMFAnnotator,
    read_sigmf_annotations,
)
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
from ..style import TEAL, heatmap_grid_color, style_figure
from .analysis import analyze
from .models import WaterfallSettings
from .plots import automatic_dbfs_ranges, waterfall_figure


COLORMAPS = ("Plasma", "Viridis", "Cividis", "Inferno", "Magma", "Turbo")


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
        minimum=0.004,
        step=0.002,
        overview=partial(power_overview, bins=300, channel=0),
        overview_label="Mean received power (dBFS)",
        time_unit="ms",
    )


def view(data: SigMFWindow, ui: UI) -> None:
    """Analyze one exact buffer and declare the complete waterfall view."""
    defaults = WaterfallSettings()
    settings = WaterfallSettings(
        fft_size=int(ui.select(
            "fft_size",
            label="FFT size (samples)",
            default=defaults.fft_size,
            options=(256, 512, 1024, 2048, 4096),
            group="Spectrogram processing",
        )),
        overlap_percent=int(ui.select(
            "overlap_percent",
            label="Overlap (%)",
            default=defaults.overlap_percent,
            options=(0, 25, 50, 75),
            group="Spectrogram processing",
        )),
    )
    products = ui.compute(
        "waterfall-analysis",
        lambda: analyze(data, settings),
    )

    colormap = ui.colormap(
        "colormap",
        label="Waterfall colormap",
        default="Plasma",
        options=COLORMAPS,
        group="Display",
    )
    automatic_waterfall, automatic_spectrum = automatic_dbfs_ranges(products)
    zmin, zmax = ui.limits(
        "dbfs_limits",
        label="Waterfall dBFS limits",
        default=automatic_waterfall,
        minimum=-140.0,
        maximum=0.0,
        step=1.0,
        group="Display",
    )
    spectrum_ymin, spectrum_ymax = ui.limits(
        "spectrum_dbfs_limits",
        label="Average power limits (dBFS)",
        default=automatic_spectrum,
        minimum=-140.0,
        maximum=0.0,
        step=1.0,
        group="Display",
    )
    spectrum_style = ui.trace_style(
        "spectrum_style",
        label="Average spectrum",
        color=TEAL,
        width=1.4,
        group="Display",
    )
    show_colorbar = ui.toggle(
        "show_colorbar",
        label="Show colorbar",
        default=True,
        group="Display",
    )
    show_annotations = ui.toggle(
        "show_annotations",
        label="Show annotations",
        default=True,
        group="Annotations",
    )
    annotation_color = ui.color(
        "annotation_region_color",
        label="Annotation color",
        default="#ffffff",
        group="Annotations",
    )
    annotation_width = float(ui.number(
        "annotation_region_width",
        label="Line weight",
        default=1.5,
        minimum=0.5,
        maximum=8.0,
        step=0.5,
        group="Annotations",
    ))
    annotation_opacity = float(ui.number(
        "annotation_region_opacity",
        label="Opacity",
        default=0.8,
        minimum=0.05,
        maximum=1.0,
        step=0.05,
        group="Annotations",
    ))
    with ui.details_group("Raster rendering"):
        render_width = int(ui.select(
            "render_width",
            label="Heatmap render width",
            default=1024,
            options=(256, 512, 1024, 2048),
        ))
        render_height = int(ui.select(
            "render_height",
            label="Heatmap render height",
            default=512,
            options=(128, 256, 512, 1024),
        ))
        aggregation = str(ui.select(
            "render_aggregation",
            label="Heatmap aggregation",
            default="mean",
            options=("max", "mean", "median"),
        ))
    title = str(
        products.recording.metadata["global"].get(
            "core:description",
            "Synthetic LTE",
        )
    )

    def figure():
        rendered = waterfall_figure(
            products,
            viewport=ui.plot_viewport("lte-waterfall"),
            colormap=colormap,
            zmin=zmin,
            zmax=zmax,
            spectrum_ymin=spectrum_ymin,
            spectrum_ymax=spectrum_ymax,
            spectrum_style=spectrum_style,
            show_colorbar=show_colorbar,
            render_width=render_width,
            render_height=render_height,
            aggregation=aggregation,
            annotations=(
                read_sigmf_annotations(products.recording)
                if show_annotations
                else ()
            ),
            annotation_color=annotation_color,
            annotation_width=annotation_width,
            annotation_opacity=annotation_opacity,
        )
        styled = style_figure(rendered, ui.theme, title)
        styled.update_xaxes(
            gridcolor=heatmap_grid_color(ui.theme),
            gridwidth=0.35,
            row=2,
            col=1,
        )
        styled.update_yaxes(
            gridcolor=heatmap_grid_color(ui.theme),
            gridwidth=0.35,
            row=2,
            col=1,
        )
        return styled

    ui.stat("Sample rate", f"{products.recording.sample_rate / 1e6:g} MS/s")
    ui.stat(
        "Center frequency",
        f"{products.recording.center_frequency_at(products.start_sample) / 1e6:g} MHz",
    )
    ui.stat("Buffer memory", format_bytes(products.buffer_nbytes))
    with ui.tab("Spectrum + waterfall"):
        ui.plot(figure, key="lte-waterfall", axis_navigation="bounded")


def create_workspace(config) -> Workspace:
    return Workspace(
        identifier="synthetic-lte-waterfall",
        name="Synthetic LTE Waterfall",
        description="Windowed spectrum and waterfall analysis of generated LTE-like uplink and downlink SigMF recordings.",
        reader=create_reader(config),
        view=view,
        annotator=WaterfallSigMFAnnotator(
            "lte-waterfall",
            "annotation_region_color",
        ),
        exporter=SigMFExporter(),
        category="spectrum monitoring",
        tags=("windowed", "synthetic", "LTE", "SigMF", "waterfall"),
        discovery_columns=SIGMF_DISCOVERY_COLUMNS,
    )


__all__ = [
    "COLORMAPS",
    "automatic_dbfs_ranges",
    "create_reader",
    "create_workspace",
    "view",
]
