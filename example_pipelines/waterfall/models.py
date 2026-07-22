"""Typed values passed between delivery, analysis, and presentation."""

from dataclasses import dataclass

import numpy as np

from ..io.sigmf import SigMFRecording


@dataclass(frozen=True)
class SignalWindow:
    recording: SigMFRecording
    start_sample: int
    samples: np.ndarray


@dataclass(frozen=True)
class WaterfallSettings:
    fft_size: int
    overlap_percent: int


@dataclass(frozen=True)
class WaterfallProducts:
    recording: SigMFRecording
    start_sample: int
    spectrum_dbfs: np.ndarray
    waterfall_dbfs: np.ndarray
    frequency_mhz: np.ndarray
    time_edges_ms: np.ndarray
    buffer_nbytes: int
