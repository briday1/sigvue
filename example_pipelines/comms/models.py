"""Typed analysis products for constellation and eye-diagram views."""

from dataclasses import dataclass

import numpy as np


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
