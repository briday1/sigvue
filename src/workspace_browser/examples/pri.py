"""PRI-oriented waterfall and max-hold analysis for stored SigMF recordings."""

from __future__ import annotations

from math import cos, log10, pi, sin
from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from workspace_browser.examples.sigmf import SigMFRecording, _describe_recording, _read_recording
from workspace_browser.examples.plot_style import ORANGE, TEAL, style_plotly
from workspace_browser.plugin import AnalysisContext, AnalysisWorkspace, DirectorySource


def analyze(recording: SigMFRecording, ui: AnalysisContext) -> None:
    """Split each playback buffer into PRI rows, then calculate waterfalls and holds."""
    buffer_seconds = ui.number("buffer_seconds", default=1.5, minimum=0.1, maximum=max(0.1, recording.duration * 0.75), step=0.1)
    pri_seconds = ui.number("pri_seconds", default=0.1, minimum=0.001, step=0.001)
    seek_seconds = ui.number("seek_seconds", default=0.1, minimum=0.001, step=0.01)
    refresh_seconds = ui.number("refresh_seconds", default=0.25, minimum=0.05, step=0.05)
    window = ui.select("spectrum_window", default="hann", options=("hann", "rectangular"))
    buffer_size = max(1, round(buffer_seconds * recording.sample_rate))
    pri_samples = max(1, round(pri_seconds * recording.sample_rate))

    time = ui.playback(
        duration=max(seek_seconds, recording.duration - buffer_size / recording.sample_rate),
        step=seek_seconds,
        refresh_interval=refresh_seconds,
    )
    offset, channels = recording.frame(time, buffer_size)
    channel_views = {
        f"Channel {index + 1}": _channel_figure(list(samples), offset, recording.sample_rate, pri_samples, window)
        for index, samples in enumerate(channels)
    }

    with ui.tab("PRI Analysis"):
        ui.view_switcher("Channel", channel_views, key="channel", selector="dropdown")

    ui.metadata.update(
        {
            "buffer_start": offset,
            "buffer_seconds": buffer_seconds,
            "buffer_samples": len(channels[0]),
            "pri_seconds": pri_seconds,
            "pri_samples": pri_samples,
            "pri_count": len(channels[0]) // pri_samples,
        }
    )
    ui.stat("Buffer", f"{buffer_seconds:g} s · {len(channels[0])} samples")
    ui.stat("PRI", f"{pri_seconds:g} s · {pri_samples} samples")
    ui.stat("Intervals per buffer", len(channels[0]) // pri_samples)
    ui.stat("Channels", len(channels))


def create_workspace(
    path: Path | None = None,
    *,
    identifier: str = "pri-waterfall",
    name: str = "PRI Waterfall Analysis",
) -> AnalysisWorkspace:
    directory = path or Path(__file__).with_name("data")
    return AnalysisWorkspace(
        identifier=identifier,
        name=name,
        description="Inspect repeated intervals as time/spectrum waterfalls with max-hold traces.",
        source=DirectorySource(directory, pattern="*.sigmf-meta", loader=_read_recording, describe=_describe_recording),
        analyze=analyze,
        category="signal analysis",
        tags=("sigmf", "waterfall", "pri", "max-hold"),
    )


def _spectra(rows: list[list[float]], sample_rate: float, window: str) -> tuple[list[float], list[list[float]]]:
    size = len(rows[0])
    frequencies = [(shifted - size // 2) * sample_rate / size for shifted in range(size)]
    spectra = []
    for row in rows:
        magnitudes = []
        for shifted in range(size):
            k = (shifted + size // 2) % size
            value = sum(
                sample
                * (0.5 - 0.5 * cos(2 * pi * index / (size - 1)) if window == "hann" else 1.0)
                * complex(cos(-2 * pi * k * index / size), sin(-2 * pi * k * index / size))
                for index, sample in enumerate(row)
            )
            magnitudes.append(20 * log10(max(abs(value) / size, 1e-9)))
        spectra.append(magnitudes)
    return frequencies, spectra


def _channel_figure(samples: list[float], offset: int, sample_rate: float, pri_samples: int, window: str) -> go.Figure:
    rows = [samples[start : start + pri_samples] for start in range(0, len(samples) - pri_samples + 1, pri_samples)]
    if not rows:
        rows = [samples]
    time_axis_ms = [index * 1_000 / sample_rate for index in range(len(rows[0]))]
    row_times = [(offset + index * pri_samples) / sample_rate for index in range(len(rows))]
    frequencies, spectra = _spectra(rows, sample_rate, window)
    time_hold = [max(abs(row[index]) for row in rows) for index in range(len(rows[0]))]
    spectrum_hold = [max(row[index] for row in spectra) for index in range(len(frequencies))]
    return _combined_figure(time_axis_ms, row_times, rows, frequencies, spectra, time_hold, spectrum_hold)


def _combined_figure(
    time_axis_ms: list[float],
    row_times: list[float],
    time_rows: list[list[float]],
    frequencies: list[float],
    spectra: list[list[float]],
    time_hold: list[float],
    spectrum_hold: list[float],
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=2,
        row_heights=(1 / 3, 2 / 3),
        vertical_spacing=0.08,
        horizontal_spacing=0.08,
        subplot_titles=("Time max hold", "Spectrum max hold", "Time waterfall", "Spectrum waterfall"),
    )
    figure.add_trace(go.Scatter(x=time_axis_ms, y=time_hold, mode="lines", name="Time max hold", line={"color": TEAL, "width": 1.5}), row=1, col=1)
    figure.add_trace(go.Scatter(x=frequencies, y=spectrum_hold, mode="lines", name="Spectrum max hold", line={"color": ORANGE, "width": 1.5}), row=1, col=2)
    figure.add_trace(go.Heatmap(x=time_axis_ms, y=row_times, z=time_rows, colorscale="Viridis", coloraxis="coloraxis", name="Time waterfall"), row=2, col=1)
    figure.add_trace(go.Heatmap(x=frequencies, y=row_times, z=spectra, colorscale="Viridis", coloraxis="coloraxis2", name="Spectrum waterfall"), row=2, col=2)
    figure.update_xaxes(title_text="Time within PRI (ms)", row=2, col=1)
    figure.update_xaxes(title_text="Frequency (Hz)", row=2, col=2)
    figure.update_yaxes(title_text="Maximum |amplitude|", row=1, col=1)
    figure.update_yaxes(title_text="Maximum magnitude (dBFS)", row=1, col=2)
    figure.update_yaxes(title_text="Buffer time (s)", row=2, col=1)
    figure.update_yaxes(title_text="Buffer time (s)", row=2, col=2)
    figure.update_layout(
        showlegend=False,
        coloraxis={"colorbar": {"title": "Amplitude", "x": 0.46, "len": 0.58, "y": 0.29}},
        coloraxis2={"colorbar": {"title": "dBFS", "x": 1.01, "len": 0.58, "y": 0.29}},
    )
    style_plotly(figure)
    return figure
