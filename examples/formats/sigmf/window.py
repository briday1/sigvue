"""Framework-independent SigMF windows and bounded power overviews."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .recording import SigMFRecording


@dataclass(frozen=True)
class SigMFWindow:
    """A channel-first sample window read from one SigMF recording."""

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


__all__ = ["SigMFWindow", "power_overview"]
