"""Domain settings and products used by waterfall analysis and plotting."""

from dataclasses import dataclass

import numpy as np

from ..formats.sigmf import SigMFRecording


@dataclass(frozen=True)
class WaterfallSettings:
    fft_size: int = 1024
    overlap_percent: int = 50


@dataclass(frozen=True)
class WaterfallProducts:
    recording: SigMFRecording
    start_sample: int
    spectrum_dbfs: np.ndarray
    waterfall_dbfs: np.ndarray
    frequency_mhz: np.ndarray
    time_edges_ms: np.ndarray
    buffer_nbytes: int
