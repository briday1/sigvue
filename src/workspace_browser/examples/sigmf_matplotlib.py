"""A file-backed SigMF workspace that emits native Matplotlib figures."""

from __future__ import annotations

from math import cos, log10, pi, sin
from pathlib import Path

from matplotlib.figure import Figure

from workspace_browser.plugin import AnalysisContext, AnalysisWorkspace, DirectorySource

from .plot_style import TEAL, style_matplotlib
from .sigmf import SigMFRecording, _describe_recording, _read_recording


def analyze(recording: SigMFRecording, ui: AnalysisContext) -> None:
    """Select a file segment and hand regular Matplotlib figures to the framework."""
    buffer_size = ui.select("buffer_size", default=128, options=(64, 128, 256))
    window = ui.select("spectrum_window", default="hann", options=("hann", "rectangular"))
    seek_seconds = ui.number("seek_seconds", default=0.1, minimum=0.001, step=0.01)
    refresh_seconds = ui.number("refresh_seconds", default=0.25, minimum=0.05, step=0.05)
    buffer_duration = min(buffer_size, recording.frame_count) / recording.sample_rate
    time = ui.playback(
        duration=max(seek_seconds, recording.duration - buffer_duration),
        step=seek_seconds,
        refresh_interval=refresh_seconds,
    )
    offset, channels = recording.frame(time, buffer_size)

    with ui.tab("Time Domain", columns=2):
        x = [(offset + index) / recording.sample_rate for index in range(len(channels[0]))]
        for index, samples in enumerate(channels):
            ui.plot(
                _time_figure(f"Channel {index + 1}", x, samples),
                key=f"time-{index}",
            )

    with ui.tab("Frequency Domain", columns=2):
        for index, samples in enumerate(channels):
            ui.plot(
                _spectrum_figure(f"Channel {index + 1}", samples, recording.sample_rate, window),
                key=f"frequency-{index}",
            )

    ui.metadata["sigmf"] = recording.metadata
    ui.stat("Renderer", "Matplotlib (PNG)")
    ui.stat("Buffer", f"{buffer_duration:g} s · {len(channels[0])} samples")
    ui.stat("Channels", len(channels))
    ui.stat("Sample rate", f"{recording.sample_rate:g} samples/s")


def create_workspace(
    path: Path | None = None,
    *,
    identifier: str = "sigmf-matplotlib-viewer",
    name: str = "SigMF Matplotlib Viewer",
) -> AnalysisWorkspace:
    directory = path or Path(__file__).with_name("data")
    return AnalysisWorkspace(
        identifier=identifier,
        name=name,
        description="Discovers stored SigMF recordings and plays them back with native Matplotlib figures.",
        source=DirectorySource(directory, pattern="*.sigmf-meta", loader=_read_recording, describe=_describe_recording),
        analyze=analyze,
        category="signal analysis",
        tags=("sigmf", "files", "playback", "matplotlib"),
    )


def _time_figure(title: str, x: list[float], samples: tuple[float, ...]) -> Figure:
    figure = Figure(figsize=(6.4, 3.6), layout="constrained")
    axes = figure.subplots()
    axes.plot(x, samples, color=TEAL, linewidth=1.25)
    return style_matplotlib(figure, axes, title=title, x_label="Recording time (s)", y_label="Amplitude")


def _spectrum_figure(title: str, samples: tuple[float, ...], sample_rate: float, window: str) -> Figure:
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

    figure = Figure(figsize=(6.4, 3.6), layout="constrained")
    axes = figure.subplots()
    axes.plot(frequencies, magnitude, color=TEAL, linewidth=1.25)
    return style_matplotlib(figure, axes, title=title, x_label="Frequency (Hz)", y_label="Magnitude (dBFS)")
