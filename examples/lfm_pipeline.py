"""Shared LFM collection loading, delivery policies, analysis, and Plotly views."""
from __future__ import annotations

from dataclasses import dataclass
import json
from math import ceil, log10, pi, sqrt
from pathlib import Path
from typing import Any

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from workspace_browser.plugin import AnalysisContext, AnalysisWorkspace, DataResource, DirectorySource, PlaybackMode, TraceStyle

from .plot_style import ORANGE, TEAL, hsv_channel_colors, style_plotly


R_OHMS = 50.0
THERMAL_NOISE_DBM_HZ = -174.0
CHANNEL_COLORS = hsv_channel_colors(4)


@dataclass(frozen=True)
class CollectionMember:
    role: str
    channel: int
    metadata_path: Path
    data_path: Path
    duration: float


@dataclass(frozen=True)
class LfmCollection:
    sample_rate: float
    calibration_dbm: float
    adc_bits: int
    members: dict[str, tuple[CollectionMember, ...]]
    ota_prf_hz: float = 1_000.0
    ota_pulse_width_seconds: float = 50e-6

    def sample_count(self, role: str) -> int:
        return min(member.data_path.stat().st_size // 4 for member in self.members[role])

    def read(self, role: str, start: int = 0, count: int | None = None) -> np.ndarray:
        available = self.sample_count(role)
        start = min(available, max(0, start))
        count = available - start if count is None else min(max(0, count), available - start)
        channels = []
        for member in self.members[role]:
            with member.data_path.open("rb") as stream:
                stream.seek(start * 4)
                iq = np.fromfile(stream, dtype="<i2", count=count * 2).reshape(-1, 2)
            channels.append(iq[:, 0].astype(np.float32) + 1j * iq[:, 1].astype(np.float32))
        return np.asarray(channels, dtype=np.complex64)


@dataclass(frozen=True)
class LfmInput:
    sample_rate: float
    calibration_dbm: float
    adc_bits: int
    pri_samples: int
    start_sample: int
    calibration_counts: np.ndarray
    noise_counts: np.ndarray
    ota_counts: np.ndarray


class BufferedDelivery:
    """Framework policy for playback: deliver one requested OTA window."""

    def __init__(self, *, playback_mode: PlaybackMode = "live") -> None:
        if playback_mode not in {"seek", "live"}:
            raise ValueError("Buffered playback mode must be 'seek' or 'live'")
        self.playback_mode = playback_mode

    def prepare(self, collection: LfmCollection, ui: AnalysisContext) -> LfmInput:
        default_pri = 1 / collection.ota_prf_hz
        buffer_seconds = ui.number("buffer_seconds", default=0.02, minimum=default_pri, maximum=0.1, step=default_pri)
        processing_prf_hz = ui.number(
            "processing_prf_hz",
            label="Processing PRF (Hz)",
            default=collection.ota_prf_hz,
            minimum=1.0,
            maximum=collection.sample_rate / 8,
            step=1.0,
        )
        seek_seconds = ui.number("seek_seconds", default=0.01, minimum=0.001, step=0.001)
        refresh_seconds = ui.number("refresh_seconds", default=0.15, minimum=0.05, step=0.05)
        available = collection.sample_count("ota")
        size = min(available, max(1, round(buffer_seconds * collection.sample_rate)))
        pri = min(size, max(8, round(collection.sample_rate / processing_prf_hz)))
        duration = max(0.0, (available - size) / collection.sample_rate)
        time = ui.playback(
            mode=self.playback_mode,
            duration=duration,
            step=seek_seconds,
            refresh_interval=refresh_seconds,
            loop=False,
        )
        start = min(round(time * collection.sample_rate), available - size)
        return _input(collection, start=start, count=size, pri=pri, ui=ui)


class WholeFileDelivery:
    """Framework policy for batch mode: deliver the complete OTA member files."""

    def __init__(self, *, default_processing_prf_hz: float | None = None) -> None:
        self.default_processing_prf_hz = default_processing_prf_hz

    def prepare(self, collection: LfmCollection, ui: AnalysisContext) -> LfmInput:
        ui.playback(mode="static")
        default_prf_hz = self.default_processing_prf_hz or collection.ota_prf_hz
        processing_prf_hz = ui.number(
            "processing_prf_hz",
            label="Processing PRF (Hz)",
            default=default_prf_hz,
            minimum=1.0,
            maximum=collection.sample_rate / 8,
            step=1.0,
        )
        pri = max(8, round(collection.sample_rate / processing_prf_hz))
        return _input(collection, start=0, count=collection.sample_count("ota"), pri=pri, ui=ui)


def _input(collection: LfmCollection, *, start: int, count: int, pri: int, ui: AnalysisContext) -> LfmInput:
    calibration = ui.once("lfm-calibration-counts", lambda: collection.read("calibration"))
    noise = ui.once("lfm-noise-counts", lambda: collection.read("terminated-noise"))
    return LfmInput(
        sample_rate=collection.sample_rate,
        calibration_dbm=collection.calibration_dbm,
        adc_bits=collection.adc_bits,
        pri_samples=pri,
        start_sample=start,
        calibration_counts=calibration,
        noise_counts=noise,
        ota_counts=collection.read("ota", start, count),
    )


def create_lfm_workspace(
    path: Path | None,
    *,
    identifier: str,
    name: str,
    delivery: BufferedDelivery | WholeFileDelivery,
) -> AnalysisWorkspace:
    directory = path or Path.cwd() / "data" / "lfm-collection"
    return AnalysisWorkspace(
        identifier=identifier,
        name=name,
        description="Manifest-defined calibration, noise, and OTA LFM collection.",
        source=DirectorySource(directory, pattern="*.sigmf-collection", loader=read_collection, describe=describe_collection),
        delivery=delivery,
        analyze=analyze_lfm,
        category="signal analysis",
        tags=("lfm", "10-mhz", "calibration", "four-channel"),
    )


def describe_collection(path: Path) -> DataResource:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return DataResource(
        path.stem,
        payload["collection"]["name"],
        source=path,
        tags=("sigmf-collection", "ci16", "four-channel"),
        summary={"members": "calibration, terminated-noise, ota"},
    )


def read_collection(path: Path) -> LfmCollection:
    payload = json.loads(path.read_text(encoding="utf-8"))
    grouped: dict[str, list[CollectionMember]] = {}
    for value in payload["members"]:
        member = CollectionMember(
            value["role"], int(value["channel"]), path.parent / value["metadata"], path.parent / value["data"], float(value["duration_seconds"])
        )
        grouped.setdefault(member.role, []).append(member)
    members = {role: tuple(sorted(values, key=lambda member: member.channel)) for role, values in grouped.items()}
    required = {"calibration", "terminated-noise", "ota"}
    if set(members) != required:
        raise ValueError(f"Collection must define exactly {sorted(required)}")
    sample_rate = float(payload["collection"]["sample_rate"])
    adc_bits = 16
    for role, records in members.items():
        if [record.channel for record in records] != [1, 2, 3, 4]:
            raise ValueError(f"{role} must define channels 1 through 4")
        for member in records:
            metadata = json.loads(member.metadata_path.read_text(encoding="utf-8"))["global"]
            if metadata.get("core:datatype") != "ci16_le" or int(metadata.get("core:num_channels", 0)) != 1:
                raise ValueError(f"{member.metadata_path.name} must be single-channel ci16_le")
            if float(metadata["core:sample_rate"]) != sample_rate:
                raise ValueError(f"{member.metadata_path.name} has a different sample rate")
            expected_bytes = round(member.duration * sample_rate) * 4
            if role != "ota" and member.data_path.stat().st_size < expected_bytes:
                raise ValueError(f"{member.data_path.name} is shorter than its declared duration")
    collection_metadata = payload["collection"]
    return LfmCollection(
        sample_rate,
        float(collection_metadata["calibration_dbm"]),
        adc_bits,
        members,
        float(collection_metadata.get("ota_prf_hz", 1_000.0)),
        float(collection_metadata.get("ota_pulse_width_seconds", 50e-6)),
    )


@dataclass(frozen=True)
class Calibration:
    phase_offsets: np.ndarray
    volts_per_count: np.ndarray
    amplitude_corrections: np.ndarray
    reference_volts_per_count: float
    phase_reference_channel: int
    amplitude_reference_channel: int
    amplitude_reference_label: str
    noise_power_dbm: np.ndarray
    noise_psd_dbm_hz: np.ndarray
    noise_figure_db: np.ndarray
    full_scale_dbm: np.ndarray


@dataclass(frozen=True)
class Products:
    fast_time_us: np.ndarray
    slow_time_s: np.ndarray
    frequencies_hz: np.ndarray
    time_mean_dbm: np.ndarray
    time_max_dbm: np.ndarray
    time_waterfall_dbm: np.ndarray
    psd_mean_dbm_hz: np.ndarray
    psd_max_dbm_hz: np.ndarray
    psd_waterfall_dbm_hz: np.ndarray


def analyze_lfm(data: LfmInput, ui: AnalysisContext) -> None:
    try:
        requested_adc_bits = int(ui.values.get("adc_bits", data.adc_bits))
    except (TypeError, ValueError):
        requested_adc_bits = data.adc_bits
    requested_adc_bits = min(32, max(2, requested_adc_bits))
    phase_reference = str(ui.values.get("phase_reference", "Channel 1"))
    amplitude_reference = str(ui.values.get("amplitude_reference", "Min"))
    calibration = _calibrate(
        data,
        adc_bits=requested_adc_bits,
        phase_reference=phase_reference,
        amplitude_reference=amplitude_reference,
    )
    trace_styles = {
        "mean": ui.trace_style("mean_trace", label="Mean / average", color=TEAL, width=1.5),
        "max": ui.trace_style("max_trace", label="Max hold", color=ORANGE, width=1.5),
        "noise": ui.trace_style("noise_trace", label="Noise reference", color="#8f9fa6", width=1.0, line_style="dot"),
        "full_scale": ui.trace_style("full_scale_trace", label="Full scale", color="#60717d", width=1.0, line_style="dash"),
    }
    ota = _apply_calibration(data.ota_counts, calibration)
    calibrated_tone = _apply_calibration(data.calibration_counts, calibration)
    calibrated_noise = data.noise_counts * calibration.volts_per_count[:, None]
    products = _products(ota, data.sample_rate, data.pri_samples, data.start_sample)

    phase_rows = [
        {
            "Channel": channel + 1,
            "Reference": "Yes" if channel == calibration.phase_reference_channel else "",
            "Phase correction": f"{-calibration.phase_offsets[channel] * 180 / pi:+.2f} deg",
        }
        for channel in range(4)
    ]
    amplitude_rows = [
        {
            "Channel": channel + 1,
            "Reference": "Yes" if channel == calibration.amplitude_reference_channel else "",
            "Calibration magnitude": f"{np.sqrt(np.mean(np.abs(data.calibration_counts[channel]) ** 2)):.1f} counts",
            "Normalization": f"{calibration.amplitude_corrections[channel]:.4f}x",
            "Scale": f"{calibration.volts_per_count[channel]:.4g} V/count",
            "Full scale": f"{calibration.full_scale_dbm[channel]:.2f} dBm",
        }
        for channel in range(4)
    ]
    with ui.tab("Waterfall"):
        ui.view_switcher(
            "Domain",
            {
                "Fast-time power": _waterfall_figure(products, "time", ui.theme),
                "Frequency PSD": _waterfall_figure(products, "frequency", ui.theme),
            },
            key="waterfall-domain",
            selector="buttons",
        )
    with ui.tab("Time Domain"):
        ui.view_switcher(
            "View",
            {
                "Multi": _time_figure(products, calibration, trace_styles, ui.theme),
                "Combined max": _combined_time_figure(products, calibration, "max", trace_styles, ui.theme),
                "Combined mean": _combined_time_figure(products, calibration, "mean", trace_styles, ui.theme),
            },
            key="time-view",
            selector="buttons",
        )
    with ui.tab("Frequency Domain"):
        ui.view_switcher(
            "View",
            {
                "Multi": _frequency_figure(products, calibration, trace_styles, ui.theme),
                "Combined max": _combined_frequency_figure(products, calibration, "max", trace_styles, ui.theme),
                "Combined mean": _combined_frequency_figure(products, calibration, "mean", trace_styles, ui.theme),
            },
            key="frequency-view",
            selector="buttons",
        )
    with ui.tab("Calibration", update="static"):
        with ui.switcher("Calibration view", key="calibration-view", selector="buttons"):
            with ui.switcher_view("Phase", columns=(0.24, 0.76)):
                with ui.group("column"):
                    with ui.parameter_group("Calibration parameters"):
                        ui.select(
                            "phase_reference",
                            label="Phase reference",
                            default="Channel 1",
                            options=("Channel 1", "Channel 2", "Channel 3", "Channel 4"),
                        )
                    ui.table(phase_rows, key="phase-diagnostics", depends_on=("phase_reference",))
                ui.plot(
                    _phase_figure(data.calibration_counts, calibration, data.sample_rate, ui.theme),
                    key="phase-plot",
                    depends_on=("phase_reference",),
                )
            with ui.switcher_view("Amplitude", columns=(0.3, 0.7)):
                with ui.group("column"):
                    with ui.parameter_group("Calibration parameters"):
                        ui.select(
                            "amplitude_reference",
                            label="Amplitude reference",
                            default="Min",
                            options=("Channel 1", "Channel 2", "Channel 3", "Channel 4", "Min"),
                        )
                        ui.number(
                            "adc_bits",
                            label="Number of ADC bits",
                            default=data.adc_bits,
                            minimum=2,
                            maximum=32,
                            step=1,
                        )
                    ui.table(amplitude_rows, key="amplitude-diagnostics", depends_on=("amplitude_reference", "adc_bits"))
                ui.plot(
                    _amplitude_figure(calibrated_tone, data, calibration, ui.theme),
                    key="amplitude-plot",
                    depends_on=("amplitude_reference", "adc_bits"),
                )
            with ui.switcher_view("Noise", columns=(0.3, 0.7)):
                with ui.group("column"):
                    with ui.parameter_group("Calibration parameters"):
                        reference_noise_psd = ui.number(
                            "reference_noise_psd_dbm_hz",
                            label="Reference PSD (dBm/Hz)",
                            default=THERMAL_NOISE_DBM_HZ,
                            minimum=-220.0,
                            maximum=-100.0,
                            step=0.1,
                        )
                        reference_lines = ui.select(
                            "noise_reference_lines",
                            label="PSD reference lines",
                            default="Expected + measured",
                            options=("Expected + measured", "Expected only", "Measured only"),
                        )
                    noise_rows = [
                        {
                            "Channel": channel + 1,
                            "Noise power": f"{calibration.noise_power_dbm[channel]:.2f} dBm",
                            "Noise PSD": f"{calibration.noise_psd_dbm_hz[channel]:.2f} dBm/Hz",
                            "Estimated NF": f"{calibration.noise_psd_dbm_hz[channel] - reference_noise_psd:.2f} dB",
                        }
                        for channel in range(4)
                    ]
                    ui.table(noise_rows, key="noise-diagnostics", depends_on=("reference_noise_psd_dbm_hz",))
                ui.plot(
                    _noise_figure(calibrated_noise, data, calibration, ui.theme, reference_noise_psd, reference_lines),
                    key="noise-plot",
                    depends_on=("reference_noise_psd_dbm_hz", "noise_reference_lines"),
                )

    ui.stat("Samples delivered", f"{data.ota_counts.shape[1]:,}")
    ui.stat("Duration delivered", f"{data.ota_counts.shape[1] / data.sample_rate:g} s")
    ui.stat("Processing PRF", f"{data.sample_rate / data.pri_samples:g} Hz")
    ui.stat("PRI", f"{data.pri_samples / data.sample_rate:g} s")
    ui.stat("Sample rate", f"{data.sample_rate / 1e6:g} MHz")


def _calibrate(
    data: LfmInput,
    *,
    adc_bits: int | None = None,
    phase_reference: str = "Channel 1",
    amplitude_reference: str = "Min",
) -> Calibration:
    instantaneous_peak_power = np.max(np.abs(data.calibration_counts) ** 2, axis=1)
    phase_reference_channel = _reference_channel(phase_reference, instantaneous_peak_power, allow_min=False)
    amplitude_reference_channel = _reference_channel(amplitude_reference, instantaneous_peak_power, allow_min=True)
    reference = data.calibration_counts[phase_reference_channel]
    phase_offsets = np.asarray([np.angle(np.mean(channel * np.conj(reference))) for channel in data.calibration_counts])
    desired_voltage = sqrt(2 * R_OHMS * 1e-3 * 10 ** (data.calibration_dbm / 10))
    count_magnitude = np.sqrt(np.mean(np.abs(data.calibration_counts) ** 2, axis=1))
    peak_magnitude = np.sqrt(np.maximum(instantaneous_peak_power, 1e-24))
    amplitude_corrections = peak_magnitude[amplitude_reference_channel] / peak_magnitude
    reference_volts_per_count = desired_voltage / max(count_magnitude[amplitude_reference_channel], 1e-12)
    volts_per_count = amplitude_corrections * reference_volts_per_count
    noise_voltage = data.noise_counts * volts_per_count[:, None]
    noise_watts = np.mean(np.abs(noise_voltage) ** 2, axis=1) / (2 * R_OHMS)
    noise_power_dbm = _db10(noise_watts / 1e-3)
    noise_psd = noise_watts / data.sample_rate
    noise_psd_dbm_hz = _db10(noise_psd / 1e-3)
    noise_figure_db = noise_psd_dbm_hz - THERMAL_NOISE_DBM_HZ
    effective_adc_bits = data.adc_bits if adc_bits is None else adc_bits
    full_scale_voltage = (2 ** (effective_adc_bits - 1) - 1) * volts_per_count
    full_scale_dbm = _db10((full_scale_voltage**2 / (2 * R_OHMS)) / 1e-3)
    reference_label = (
        f"Min (Channel {amplitude_reference_channel + 1})"
        if amplitude_reference == "Min"
        else f"Channel {amplitude_reference_channel + 1}"
    )
    return Calibration(
        phase_offsets,
        volts_per_count,
        amplitude_corrections,
        reference_volts_per_count,
        phase_reference_channel,
        amplitude_reference_channel,
        reference_label,
        noise_power_dbm,
        noise_psd_dbm_hz,
        noise_figure_db,
        full_scale_dbm,
    )


def _reference_channel(value: str, peak_power: np.ndarray, *, allow_min: bool) -> int:
    if allow_min and value == "Min":
        return int(np.argmin(peak_power))
    if value.startswith("Channel "):
        try:
            index = int(value.removeprefix("Channel ")) - 1
        except ValueError:
            index = 0
        if 0 <= index < peak_power.size:
            return index
    return 0


def _apply_calibration(counts: np.ndarray, calibration: Calibration) -> np.ndarray:
    rotations = np.exp(-1j * calibration.phase_offsets).astype(np.complex64)
    normalized = counts * calibration.amplitude_corrections[:, None]
    return normalized * calibration.reference_volts_per_count * rotations[:, None]


def _products(
    channels: np.ndarray,
    rate: float,
    pri: int,
    start: int,
    max_rows: int = 384,
    max_fast_time_bins: int = 512,
    max_frequency_bins: int = 512,
) -> Products:
    row_count = channels.shape[1] // pri
    if row_count < 1:
        raise ValueError("Delivered data must contain at least one PRI")
    rows = channels[:, : row_count * pri].reshape(4, row_count, pri)
    fast_group_size = max(1, ceil(pri / max_fast_time_bins))
    displayed_samples = pri // fast_group_size * fast_group_size
    fast_time_start = start % pri
    fast_time = (
        (fast_time_start + np.arange(0, displayed_samples, fast_group_size))
        / rate
        * 1e6
    )
    power = np.abs(rows) ** 2 / (2 * R_OHMS)
    mean_power = np.mean(power, axis=1)[:, :displayed_samples]
    max_power = np.max(power, axis=1)[:, :displayed_samples]
    time_mean = _db10(mean_power.reshape(4, -1, fast_group_size).mean(axis=2) / 1e-3)
    time_max = _db10(max_power.reshape(4, -1, fast_group_size).max(axis=2) / 1e-3)

    # Transform every sample in each PRI. Truncating the row here makes a
    # shifted pulse disappear from the PSD even though it remains visible in
    # the time waterfall. A rectangular full-row periodogram also preserves
    # spectral magnitude under circular shifts; display reduction happens only
    # after power has been calculated for every FFT bin.
    frequency_group_size = max(1, ceil(pri / max_frequency_bins))
    full_frequencies = np.fft.fftshift(np.fft.fftfreq(pri, d=1 / rate))
    frequencies = _group_mean(full_frequencies, frequency_group_size)
    frequency_bin_hz = rate / pri
    psd_sum = np.zeros((4, frequencies.size), dtype=np.float64)
    psd_max = np.zeros((4, frequencies.size), dtype=np.float64)
    time_waterfall = []
    psd_waterfall = []
    slow_time = []
    group_size = max(1, ceil(row_count / max_rows))
    for first in range(0, row_count, group_size):
        block = rows[:, first : min(first + group_size, row_count)]
        block_power = np.abs(block) ** 2 / (2 * R_OHMS)
        waterfall_power = np.mean(block_power, axis=1)[:, :displayed_samples]
        waterfall_power = waterfall_power.reshape(4, -1, fast_group_size).mean(axis=2)
        time_waterfall.append(_db10(waterfall_power / 1e-3))
        spectrum = np.fft.fftshift(np.fft.fft(block, axis=2), axes=2)
        full_psd = np.abs(spectrum) ** 2 / pri**2 / (2 * R_OHMS) / frequency_bin_hz
        psd = _group_mean(full_psd, frequency_group_size)
        psd_sum += np.sum(psd, axis=1)
        psd_max = np.maximum(psd_max, np.max(psd, axis=1))
        psd_waterfall.append(_db10(np.mean(psd, axis=1) / 1e-3))
        slow_time.append((first + block.shape[1] / 2) * pri / rate)
    psd_mean = _db10((psd_sum / row_count) / 1e-3)
    psd_hold = _db10(psd_max / 1e-3)
    return Products(
        fast_time,
        np.asarray(slow_time),
        frequencies,
        time_mean,
        time_max,
        np.stack(time_waterfall, axis=1),
        psd_mean,
        psd_hold,
        np.stack(psd_waterfall, axis=1),
    )


def _group_mean(values: np.ndarray, group_size: int) -> np.ndarray:
    """Average adjacent values on the final axis without dropping a tail."""
    if group_size <= 1:
        return values
    starts = np.arange(0, values.shape[-1], group_size)
    counts = np.diff(np.append(starts, values.shape[-1]))
    shape = (1,) * (values.ndim - 1) + (counts.size,)
    return np.add.reduceat(values, starts, axis=-1) / counts.reshape(shape)


def _db10(value: Any) -> np.ndarray:
    return 10 * np.log10(np.maximum(value, 1e-30))


def _phase_figure(
    counts: np.ndarray,
    calibration: Calibration,
    sample_rate: float,
    theme: str,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=2,
        specs=[[{"colspan": 2}, None], [{}, {}]],
        row_heights=(0.42, 0.58),
        subplot_titles=("Amplitude", "Phase before", "Phase aligned"),
    )
    subset = counts[:, :512]
    time_us = np.arange(subset.shape[1]) / sample_rate * 1e6
    aligned = subset * np.exp(-1j * calibration.phase_offsets)[:, None]
    for channel in range(4):
        name = f"Channel {channel + 1}"
        line = {"color": CHANNEL_COLORS[channel]}
        figure.add_trace(go.Scatter(x=time_us, y=np.abs(subset[channel]), name=name, line=line), row=1, col=1)
        figure.add_trace(go.Scatter(x=time_us, y=np.unwrap(np.angle(subset[channel])), name=name, line=line, showlegend=False), row=2, col=1)
        figure.add_trace(go.Scatter(x=time_us, y=np.unwrap(np.angle(aligned[channel])), name=name, line=line, showlegend=False), row=2, col=2)
    figure.update_xaxes(title_text="Time (us)")
    return style_plotly(
        figure,
        title=f"Phase calibration · reference Channel {calibration.phase_reference_channel + 1}",
        theme=theme,
        boxed_axes=True,
    )


def _amplitude_figure(
    channels: np.ndarray,
    data: LfmInput,
    calibration: Calibration,
    theme: str,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        subplot_titles=("Signal power", "Signal PSD"),
    )
    subset = channels[:, : min(4096, channels.shape[1])]
    time_us = np.arange(subset.shape[1]) / data.sample_rate * 1e6
    for channel in range(4):
        power = _db10((np.abs(subset[channel]) ** 2 / (2 * R_OHMS)) / 1e-3)
        frequency, psd = _single_psd(subset[channel], data.sample_rate)
        line = {"color": CHANNEL_COLORS[channel]}
        figure.add_trace(go.Scatter(x=time_us, y=power, name=f"Channel {channel + 1}", line=line), row=1, col=1)
        figure.add_trace(go.Scatter(x=frequency, y=psd, name=f"Channel {channel + 1}", line=line, showlegend=False), row=2, col=1)
    figure.add_trace(go.Scatter(x=[time_us[0], time_us[-1]], y=[data.calibration_dbm] * 2, name="Incident power", line={"color": ORANGE, "dash": "dash"}), row=1, col=1)
    figure.update_xaxes(title_text="Time (us)", row=1, col=1)
    figure.update_xaxes(title_text="Frequency (Hz)", row=2, col=1)
    return style_plotly(
        figure,
        title=f"Amplitude calibration · reference {calibration.amplitude_reference_label}",
        theme=theme,
        boxed_axes=True,
    )


def _noise_figure(
    channels: np.ndarray,
    data: LfmInput,
    calibration: Calibration,
    theme: str,
    reference_noise_psd: float = THERMAL_NOISE_DBM_HZ,
    reference_lines: str = "Expected + measured",
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        subplot_titles=("Instantaneous noise power", "Averaged noise PSD"),
    )
    subset = channels[:, : min(4096, channels.shape[1])]
    time_us = np.arange(subset.shape[1]) / data.sample_rate * 1e6
    for channel in range(4):
        power = _db10((np.abs(subset[channel]) ** 2 / (2 * R_OHMS)) / 1e-3)
        frequency, psd = _averaged_psd(channels[channel], data.sample_rate)
        line = {"color": CHANNEL_COLORS[channel]}
        figure.add_trace(go.Scatter(x=time_us, y=power, name=f"Channel {channel + 1}", line=line), row=1, col=1)
        figure.add_trace(go.Scatter(x=frequency, y=psd, name=f"Channel {channel + 1}", line=line, showlegend=False), row=2, col=1)
    if reference_lines != "Expected only":
        for channel in range(4):
            figure.add_trace(go.Scatter(x=[-data.sample_rate / 2, data.sample_rate / 2], y=[calibration.noise_psd_dbm_hz[channel]] * 2, name=f"Ch {channel + 1} measured floor", line={"dash": "dot"}), row=2, col=1)
    if reference_lines != "Measured only":
        figure.add_trace(
            go.Scatter(
                x=[-data.sample_rate / 2, data.sample_rate / 2],
                y=[reference_noise_psd] * 2,
                name="Expected noise PSD",
                line={"color": ORANGE, "dash": "dash"},
            ),
            row=2,
            col=1,
        )
    figure.update_xaxes(title_text="Time (us)", row=1, col=1)
    figure.update_xaxes(title_text="Frequency (Hz)", row=2, col=1)
    return style_plotly(figure, title="Terminated-noise calibration", theme=theme, boxed_axes=True)


def _single_psd(samples: np.ndarray, rate: float) -> tuple[np.ndarray, np.ndarray]:
    nfft = min(1024, samples.size)
    window = np.hanning(nfft)
    spectrum = np.fft.fftshift(np.fft.fft(samples[:nfft] * window))
    psd = np.abs(spectrum) ** 2 / (rate * np.sum(window**2) * 2 * R_OHMS)
    return np.fft.fftshift(np.fft.fftfreq(nfft, d=1 / rate)), _db10(psd / 1e-3)


def _averaged_psd(
    samples: np.ndarray,
    rate: float,
    *,
    nfft: int = 1024,
    max_blocks: int = 256,
) -> tuple[np.ndarray, np.ndarray]:
    nfft = min(nfft, samples.size)
    block_count = samples.size // nfft
    if block_count < 1:
        return _single_psd(samples, rate)
    stride = max(1, block_count // max_blocks)
    blocks = samples[: block_count * nfft].reshape(block_count, nfft)[::stride][:max_blocks]
    window = np.hanning(nfft)
    spectra = np.fft.fftshift(np.fft.fft(blocks * window, axis=1), axes=1)
    psd = np.mean(np.abs(spectra) ** 2, axis=0) / (rate * np.sum(window**2) * 2 * R_OHMS)
    frequencies = np.fft.fftshift(np.fft.fftfreq(nfft, d=1 / rate))
    return frequencies, _db10(psd / 1e-3)


def _waterfall_figure(products: Products, domain: str, theme: str) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=2,
        shared_xaxes="all",
        shared_yaxes="all",
        subplot_titles=[f"Channel {channel + 1}" for channel in range(4)],
    )
    for channel in range(4):
        if domain == "time":
            x, z, title = products.fast_time_us, products.time_waterfall_dbm[channel], "Power (dBm)"
        else:
            x, z, title = products.frequencies_hz, products.psd_waterfall_dbm_hz[channel], "PSD (dBm/Hz)"
        figure.add_trace(go.Heatmap(x=x, y=products.slow_time_s, z=z, colorscale="Viridis", showscale=channel == 3, colorbar={"title": title}), row=channel // 2 + 1, col=channel % 2 + 1)
    figure.update_yaxes(title_text="Relative slow time (s)", col=1)
    figure.update_xaxes(title_text="Fast time (us)" if domain == "time" else "Frequency (Hz)", row=2)
    return style_plotly(
        figure,
        title="Fast-time power waterfall" if domain == "time" else "Frequency PSD waterfall",
        theme=theme,
        boxed_axes=True,
    )


def _time_figure(
    products: Products,
    calibration: Calibration,
    trace_styles: dict[str, TraceStyle],
    theme: str,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=2,
        shared_xaxes="all",
        subplot_titles=[f"Channel {channel + 1}" for channel in range(4)],
    )
    for channel in range(4):
        row, col = channel // 2 + 1, channel % 2 + 1
        x = products.fast_time_us
        traces = (
            (products.time_mean_dbm[channel], "Mean", trace_styles["mean"]),
            (products.time_max_dbm[channel], "Max hold", trace_styles["max"]),
            (np.full(x.size, calibration.noise_power_dbm[channel]), "Noise power", trace_styles["noise"]),
            (np.full(x.size, calibration.full_scale_dbm[channel]), "Full scale", trace_styles["full_scale"]),
        )
        for y, name, trace_style in traces:
            figure.add_trace(
                go.Scatter(
                    x=x,
                    y=y,
                    name=name,
                    mode=trace_style.mode,
                    line=trace_style.line,
                    marker=trace_style.plotly_marker,
                    showlegend=channel == 0,
                ),
                row=row,
                col=col,
            )
    figure.update_xaxes(title_text="Fast time (us)", row=2)
    figure.update_yaxes(title_text="Power (dBm)", col=1)
    return style_plotly(figure, title="Fast-time mean and max hold", theme=theme, boxed_axes=True)


def _combined_time_figure(
    products: Products,
    calibration: Calibration,
    aggregation: str,
    trace_styles: dict[str, TraceStyle],
    theme: str,
) -> go.Figure:
    values = products.time_max_dbm if aggregation == "max" else products.time_mean_dbm
    label = "Max hold" if aggregation == "max" else "Mean"
    figure = _combined_channel_figure(
        products.fast_time_us,
        values,
        calibration.noise_power_dbm,
        calibration.full_scale_dbm,
        label,
        "Noise power",
        trace_styles[aggregation],
        trace_styles,
    )
    figure.update_xaxes(title_text="Fast time (us)")
    figure.update_yaxes(title_text="Power (dBm)")
    return style_plotly(figure, title=f"Combined fast-time {label.lower()}", theme=theme, boxed_axes=True)


def _frequency_figure(
    products: Products,
    calibration: Calibration,
    trace_styles: dict[str, TraceStyle],
    theme: str,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=2,
        shared_xaxes="all",
        subplot_titles=[f"Channel {channel + 1}" for channel in range(4)],
    )
    for channel in range(4):
        row, col = channel // 2 + 1, channel % 2 + 1
        x = products.frequencies_hz
        traces = (
            (products.psd_mean_dbm_hz[channel], "Average", trace_styles["mean"]),
            (products.psd_max_dbm_hz[channel], "Max hold", trace_styles["max"]),
            (np.full(x.size, calibration.noise_psd_dbm_hz[channel]), "Noise PSD", trace_styles["noise"]),
            (np.full(x.size, calibration.full_scale_dbm[channel]), "Full scale", trace_styles["full_scale"]),
        )
        for y, name, trace_style in traces:
            figure.add_trace(
                go.Scatter(
                    x=x,
                    y=y,
                    name=name,
                    mode=trace_style.mode,
                    line=trace_style.line,
                    marker=trace_style.plotly_marker,
                    showlegend=channel == 0,
                ),
                row=row,
                col=col,
            )
    figure.update_xaxes(title_text="Frequency (Hz)", row=2)
    figure.update_yaxes(title_text="PSD (dBm/Hz)", col=1)
    return style_plotly(figure, title="Average and max-hold PSD", theme=theme, boxed_axes=True)


def _combined_frequency_figure(
    products: Products,
    calibration: Calibration,
    aggregation: str,
    trace_styles: dict[str, TraceStyle],
    theme: str,
) -> go.Figure:
    values = products.psd_max_dbm_hz if aggregation == "max" else products.psd_mean_dbm_hz
    label = "Max hold" if aggregation == "max" else "Mean"
    figure = _combined_channel_figure(
        products.frequencies_hz,
        values,
        calibration.noise_psd_dbm_hz,
        calibration.full_scale_dbm,
        label,
        "Noise PSD",
        trace_styles[aggregation],
        trace_styles,
    )
    figure.update_xaxes(title_text="Frequency (Hz)")
    figure.update_yaxes(title_text="PSD (dBm/Hz)")
    return style_plotly(figure, title=f"Combined {label.lower()} PSD", theme=theme, boxed_axes=True)


def _combined_channel_figure(
    x: np.ndarray,
    values: np.ndarray,
    noise_values: np.ndarray,
    full_scale_values: np.ndarray,
    value_label: str,
    noise_label: str,
    value_style: TraceStyle,
    trace_styles: dict[str, TraceStyle],
) -> go.Figure:
    """Overlay channel results and their references while retaining channel identity."""
    figure = go.Figure()
    for channel, color in enumerate(CHANNEL_COLORS):
        channel_name = f"Channel {channel + 1}"
        figure.add_trace(
            go.Scatter(
                x=x,
                y=values[channel],
                name=f"{channel_name} {value_label}",
                mode=value_style.mode,
                line={**value_style.line, "color": color},
                marker={**value_style.plotly_marker, "color": color},
                legendgroup=channel_name,
            )
        )
        for reference, reference_label, reference_style in (
            (noise_values[channel], noise_label, trace_styles["noise"]),
            (full_scale_values[channel], "Full scale", trace_styles["full_scale"]),
        ):
            figure.add_trace(
                go.Scatter(
                    x=x,
                    y=np.full(x.size, reference),
                    name=f"{channel_name} {reference_label}",
                    mode="lines",
                    line={**reference_style.line, "color": color},
                    legendgroup=channel_name,
                )
            )
    return figure
