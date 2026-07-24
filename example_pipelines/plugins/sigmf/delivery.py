"""Reusable window selection, ranged reads, and power overviews for SigMF."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np

from sigvue.plugin import Delivery, DeliveryContext, TimeUnit

from .recording import SigMFRecording


@dataclass(frozen=True)
class SigMFWindow:
    """A channel-first sample window delivered from one SigMF recording."""

    recording: SigMFRecording
    start_sample: int
    samples: np.ndarray

    def __post_init__(self) -> None:
        if isinstance(self.start_sample, bool) or not isinstance(
            self.start_sample,
            int,
        ):
            raise TypeError("start_sample must be an integer")
        if not 0 <= self.start_sample <= self.recording.sample_count:
            raise ValueError("Window start is outside the recording")
        if not isinstance(self.samples, np.ndarray) or self.samples.ndim != 2:
            raise ValueError(
                "Window samples must be a channel-first two-dimensional array"
            )
        if self.samples.shape[0] != self.recording.channel_count:
            raise ValueError(
                "Window samples do not match the recording channel count"
            )
        if self.start_sample + self.samples.shape[-1] > self.recording.sample_count:
            raise ValueError("Window samples extend beyond the recording")

    @property
    def sample_count(self) -> int:
        return int(self.samples.shape[-1])

    @property
    def start_seconds(self) -> float:
        return self.start_sample / self.recording.sample_rate

    @property
    def duration_seconds(self) -> float:
        return self.sample_count / self.recording.sample_rate

    @property
    def buffer_nbytes(self) -> int:
        return int(self.samples.nbytes)

    def channel(self, index: int = 0) -> np.ndarray:
        """Return one channel without changing the canonical stored shape."""
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("channel index must be an integer")
        if not 0 <= index < self.recording.channel_count:
            raise ValueError("channel index is outside the recording")
        return self.samples[index]


def power_overview(
    recording: SigMFRecording,
    *,
    bins: int = 300,
    channel: int | None = None,
) -> np.ndarray:
    """Compute mean power per source interval using bounded ranged reads."""
    if isinstance(bins, bool) or not isinstance(bins, int) or bins < 1:
        raise ValueError("bins must be positive")
    if channel is not None and (
        isinstance(channel, bool)
        or not isinstance(channel, int)
        or not 0 <= channel < recording.channel_count
    ):
        raise ValueError("overview channel is outside the recording")
    count = min(bins, recording.sample_count)
    if count == 0:
        return np.empty(0, dtype=np.float64)
    edges = np.linspace(
        0,
        recording.sample_count,
        count + 1,
        dtype=np.int64,
    )
    values = []
    for start, stop in zip(edges[:-1], edges[1:]):
        samples = recording.read(int(start), int(stop - start))
        selected = samples if channel is None else samples[channel]
        power = float(np.mean(np.abs(selected) ** 2))
        values.append(10 * np.log10(max(power, 1e-12)))
    return np.asarray(values)


class WindowedSigMFDelivery(Delivery[SigMFRecording, SigMFWindow]):
    """Configurable, drop-in windowed delivery for a SigMF source."""

    def __init__(
        self,
        *,
        default_window: float,
        minimum_window: float,
        step: float,
        overview_bins: int = 300,
        overview_channel: int | None = None,
        overview_label: str = "Mean received power (dBFS)",
        time_unit: TimeUnit = "s",
        cache_key: str = "sigmf-power",
    ) -> None:
        if (
            not all(isfinite(value) for value in (
                default_window,
                minimum_window,
                step,
            ))
            or min(default_window, minimum_window, step) <= 0
        ):
            raise ValueError(
                "SigMF window durations and step must be finite and positive"
            )
        if (
            isinstance(overview_bins, bool)
            or not isinstance(overview_bins, int)
            or overview_bins < 1
        ):
            raise ValueError("overview_bins must be positive")
        if overview_channel is not None and (
            isinstance(overview_channel, bool)
            or not isinstance(overview_channel, int)
            or overview_channel < 0
        ):
            raise ValueError("overview_channel must be a non-negative integer")
        if not cache_key:
            raise ValueError("cache_key cannot be empty")
        self.default_window = default_window
        self.minimum_window = minimum_window
        self.step = step
        self.overview_bins = overview_bins
        self.overview_channel = overview_channel
        self.overview_label = overview_label
        self.time_unit = time_unit
        self.cache_key = cache_key

    def prepare(
        self,
        recording: SigMFRecording,
        ui: DeliveryContext,
    ) -> SigMFWindow:
        overview = ui.once(
            f"{self.cache_key}:{recording.metadata_path}",
            lambda: power_overview(
                recording,
                bins=self.overview_bins,
                channel=self.overview_channel,
            ),
        )
        start_seconds, end_seconds = ui.windowed(
            duration=recording.duration_seconds,
            default_window=min(self.default_window, recording.duration_seconds),
            minimum_window=min(
                self.minimum_window,
                recording.duration_seconds,
            ),
            step=min(self.step, recording.duration_seconds),
            overview=overview,
            overview_label=self.overview_label,
            time_unit=self.time_unit,
        )
        start_sample = round(start_seconds * recording.sample_rate)
        sample_count = max(
            1,
            round((end_seconds - start_seconds) * recording.sample_rate),
        )
        return SigMFWindow(
            recording,
            start_sample,
            recording.read(start_sample, sample_count),
        )
