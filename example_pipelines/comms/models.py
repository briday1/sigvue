"""Typed analysis products for constellation and eye-diagram views."""

from dataclasses import dataclass

import numpy as np

from ..io.sigmf import SigMFRecording


@dataclass(frozen=True)
class CommsWindow:
    recording: SigMFRecording
    start_sample: int
    samples: np.ndarray

    @property
    def start_seconds(self) -> float:
        return self.start_sample / self.recording.sample_rate

    @property
    def duration_seconds(self) -> float:
        return self.samples.size / self.recording.sample_rate


@dataclass(frozen=True)
class CommsProducts:
    modulation: str
    samples_per_symbol: int
    symbols: np.ndarray
    eye_time: np.ndarray
    eye_segments: np.ndarray
    constellation_limit: float
    eye_limit: float
    start_seconds: float
    duration_seconds: float
    buffer_nbytes: int
