"""A complete file-backed plugin using the high-level source + analysis API."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from math import atan2, cos, log10, pi, sin
from pathlib import Path

import plotly.graph_objects as go

from workspace_browser.plugin import AnalysisContext, AnalysisWorkspace, DataResource, DirectorySource

from .plot_style import TEAL, style_plotly


@dataclass(frozen=True)
class SigMFRecording:
    metadata_path: Path
    data_path: Path
    sample_rate: float
    datatype: str
    channel_count: int
    frame_count: int
    metadata: dict[str, object]

    @property
    def duration(self) -> float:
        return self.frame_count / self.sample_rate

    @property
    def bytes_per_frame(self) -> int:
        scalar_format, components = _FORMATS[self.datatype]
        return struct.calcsize(scalar_format) * components * self.channel_count

    def frame(self, time: float, size: int) -> tuple[int, tuple[tuple[float, ...], ...]]:
        """Seek to and read only one interleaved analysis frame from the data file."""
        scalar_format, components = _FORMATS[self.datatype]
        bytes_per_frame = self.bytes_per_frame
        size = min(size, self.frame_count)
        offset = min(int(time * self.sample_rate), self.frame_count - size)
        with self.data_path.open("rb") as stream:
            stream.seek(offset * bytes_per_frame)
            payload = stream.read(size * bytes_per_frame)
        scalars = [value[0] for value in struct.iter_unpack(scalar_format, payload)]
        values = [
            scalars[index] if components == 1 else abs(complex(scalars[index], scalars[index + 1]))
            for index in range(0, len(scalars), components)
        ]
        return offset, tuple(tuple(values[index::self.channel_count]) for index in range(self.channel_count))


def analyze(recording: SigMFRecording, ui: AnalysisContext) -> None:
    """This is the plugin: select a frame, process it, and emit native plots."""
    buffer_size = ui.select("buffer_size", default=128, options=(64, 128, 256))
    window = ui.select("spectrum_window", default="hann", options=("hann", "rectangular"))
    seek_seconds = ui.number("seek_seconds", default=0.1, minimum=0.001, step=0.01)
    refresh_seconds = ui.number("refresh_seconds", default=0.25, minimum=0.05, step=0.05)
    buffer_duration = min(buffer_size, recording.frame_count) / recording.sample_rate
    time = ui.playback(
        mode="seek",
        duration=max(seek_seconds, recording.duration - buffer_duration),
        step=seek_seconds,
        refresh_interval=refresh_seconds,
    )
    offset, channels = recording.frame(time, buffer_size)
    calibration_size = min(256, recording.frame_count)
    calibration_channels = ui.once(
        "calibration-frame",
        lambda: recording.frame(0, calibration_size)[1],
    )
    calibration = ui.once(
        "phase-calibration",
        lambda: _phase_calibration(calibration_channels, recording.sample_rate),
    )

    with ui.tab("Calibration", columns=(1, 2), update="static"):
        with ui.group("column"):
            ui.text(
                "# Phase calibration\n"
                "Reference: Channel 1\n"
                f"Dominant tone: {calibration['frequency_hz']:.3g} Hz\n"
                f"Calibration window: first {calibration_size} samples",
                key="calibration-diagnostics",
            )
            ui.table(calibration["diagnostics"], key="calibration-table")
        with ui.group("column"):
            ui.plot(
                lambda: _phase_alignment_figure(calibration_channels, calibration["aligned"], recording.sample_rate),
                key="calibration-alignment",
            )

    with ui.tab("Time Domain", columns=2):
        x = [(offset + index) / recording.sample_rate for index in range(len(channels[0]))]
        for index, samples in enumerate(channels):
            ui.plot(_line_figure(f"Channel {index + 1}", "Recording time (s)", "Amplitude", x, list(samples)), key=f"time-{index}")

    with ui.tab("Frequency Domain", columns=2):
        for index, samples in enumerate(channels):
            ui.plot(_spectrum_figure(f"Channel {index + 1}", list(samples), recording.sample_rate, window), key=f"frequency-{index}")

    ui.metadata["sigmf"] = recording.metadata
    ui.stat("Buffer", f"{buffer_duration:g} s · {len(channels[0])} samples")
    ui.stat("Channels", len(channels))
    ui.stat("Sample rate", f"{recording.sample_rate:g} samples/s")
    ui.stat("Calibration", f"first {calibration_size} samples · cached")


def create_workspace(
    path: Path | None = None,
    *,
    identifier: str = "sigmf-viewer",
    name: str = "SigMF File Viewer",
) -> AnalysisWorkspace:
    directory = path or Path(__file__).with_name("data")
    return AnalysisWorkspace(
        identifier=identifier,
        name=name,
        description="Discovers stored SigMF recordings and plays them back with native Plotly views.",
        source=DirectorySource(directory, pattern="*.sigmf-meta", loader=_read_recording, describe=_describe_recording),
        analyze=analyze,
        category="signal analysis",
        tags=("sigmf", "files", "playback"),
    )


def _describe_recording(metadata_path: Path) -> DataResource:
    """Optional SigMF-specific columns for the framework-owned directory listing."""
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    global_metadata = metadata.get("global", {})
    data_path = _data_path(metadata_path)
    channel_count = int(global_metadata.get("core:num_channels", 1))
    channel_label = "channel" if channel_count == 1 else "channels"
    return DataResource(
        identifier=metadata_path.name.removesuffix(".sigmf-meta"),
        title=global_metadata.get("core:description", metadata_path.stem),
        source=metadata_path,
        subtitle=f"{channel_count} {channel_label} · {global_metadata.get('core:sample_rate', '?')} samples/s",
        timestamp=datetime.fromtimestamp(metadata_path.stat().st_mtime, tz=timezone.utc),
        tags=("sigmf", global_metadata.get("core:datatype", "unknown")),
        summary={"metadata": metadata_path.name, "data": data_path.name},
    )


def _data_path(metadata_path: Path) -> Path:
    return metadata_path.with_name(metadata_path.name.removesuffix(".sigmf-meta") + ".sigmf-data")


def _read_recording(metadata_path: Path) -> SigMFRecording:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    global_metadata = metadata["global"]
    datatype = global_metadata["core:datatype"]
    channel_count = int(global_metadata.get("core:num_channels", 1))
    if datatype not in _FORMATS:
        raise ValueError(f"Unsupported SigMF datatype: {datatype}")
    scalar_format, components = _FORMATS[datatype]
    scalar_size = struct.calcsize(scalar_format)
    data_path = _data_path(metadata_path)
    bytes_per_frame = scalar_size * components * channel_count
    frame_count = data_path.stat().st_size // bytes_per_frame
    return SigMFRecording(
        metadata_path,
        data_path,
        float(global_metadata["core:sample_rate"]),
        datatype,
        channel_count,
        frame_count,
        metadata,
    )


_FORMATS = {"rf32_le": ("<f", 1), "rf32_be": (">f", 1), "cf32_le": ("<f", 2), "cf32_be": (">f", 2)}


def _line_figure(title: str, x_label: str, y_label: str, x: list[float], y: list[float]) -> go.Figure:
    figure = go.Figure(go.Scatter(x=x, y=y, mode="lines", name=title, line={"color": TEAL, "width": 1.5}))
    figure.update_xaxes(title_text=x_label)
    figure.update_yaxes(title_text=y_label)
    style_plotly(figure, title=title)
    return figure


def _spectrum_figure(title: str, samples: list[float], sample_rate: float, window: str) -> go.Figure:
    size = min(128, len(samples))
    frequencies, magnitude = [], []
    for shifted in range(size):
        k = (shifted + size // 2) % size
        value = sum(
            sample
            * (0.5 - 0.5 * cos(2 * pi * index / (size - 1)) if window == "hann" else 1.0)
            * complex(cos(-2 * pi * k * index / size), sin(-2 * pi * k * index / size))
            for index, sample in enumerate(samples[:size])
        )
        frequencies.append((shifted - size // 2) * sample_rate / size)
        magnitude.append(20 * log10(max(abs(value) / size, 1e-9)))
    return _line_figure(title, "Frequency (Hz)", "Magnitude (dBFS)", frequencies, magnitude)


def _phase_calibration(channels: tuple[tuple[float, ...], ...], sample_rate: float) -> dict[str, object]:
    """Estimate channel phase at the reference channel's dominant non-DC tone."""
    size = len(channels[0])

    def coefficient(samples: tuple[float, ...], bin_index: int) -> complex:
        return sum(
            sample * complex(cos(-2 * pi * bin_index * index / size), sin(-2 * pi * bin_index * index / size))
            for index, sample in enumerate(samples)
        )

    dominant_bin = max(range(1, max(2, size // 2)), key=lambda index: abs(coefficient(channels[0], index)))
    reference_phase = atan2(coefficient(channels[0], dominant_bin).imag, coefficient(channels[0], dominant_bin).real)
    diagnostics = []
    aligned = []
    for index, samples in enumerate(channels):
        value = coefficient(samples, dominant_bin)
        phase = (atan2(value.imag, value.real) - reference_phase + pi) % (2 * pi) - pi
        shift = round(-phase * size / (2 * pi * dominant_bin))
        corrected = tuple(samples[(sample_index + shift) % size] for sample_index in range(size))
        corrected_value = coefficient(corrected, dominant_bin)
        residual = (atan2(corrected_value.imag, corrected_value.real) - reference_phase + pi) % (2 * pi) - pi
        diagnostics.append(
            {
                "Channel": index + 1,
                "Measured offset": f"{phase * 180 / pi:+.1f}°",
                "Applied shift": f"{shift:+d} samples",
                "Residual": f"{residual * 180 / pi:+.1f}°",
            }
        )
        aligned.append(corrected)
    return {
        "frequency_hz": dominant_bin * sample_rate / size,
        "diagnostics": diagnostics,
        "aligned": tuple(aligned),
    }


def _phase_alignment_figure(
    before: tuple[tuple[float, ...], ...],
    after: tuple[tuple[float, ...], ...],
    sample_rate: float,
) -> go.Figure:
    from plotly.subplots import make_subplots

    count = min(96, len(before[0]))
    time_ms = [index * 1_000 / sample_rate for index in range(count)]
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, subplot_titles=("Before phase alignment", "After phase alignment"))
    for index, (raw, aligned) in enumerate(zip(before, after)):
        name = f"Channel {index + 1}"
        figure.add_trace(go.Scatter(x=time_ms, y=raw[:count], mode="lines", name=name, legendgroup=name), row=1, col=1)
        figure.add_trace(
            go.Scatter(x=time_ms, y=aligned[:count], mode="lines", name=name, legendgroup=name, showlegend=False),
            row=2,
            col=1,
        )
    figure.update_xaxes(title_text="Calibration time (ms)", row=2, col=1)
    figure.update_yaxes(title_text="Amplitude", row=1, col=1)
    figure.update_yaxes(title_text="Amplitude", row=2, col=1)
    style_plotly(figure, title="Calibration effect")
    return figure
