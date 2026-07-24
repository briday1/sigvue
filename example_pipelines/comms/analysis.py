"""Recover symbol samples and eye traces from the selected recording window."""

import numpy as np
from sigvue.plugin import Analysis

from ..plugins.sigmf import SigMFWindow

from .models import CommsProducts


class CommsAnalysis(Analysis[SigMFWindow, None, CommsProducts]):
    """Recover communications products from the selected sample window."""

    def process(self, window: SigMFWindow, settings: None) -> CommsProducts:
        samples = window.channel()
        metadata = window.recording.metadata["global"]
        modulation = str(
            metadata.get("example:modulation", "Unknown")
        )
        samples_per_symbol = int(
            metadata.get("example:samples_per_symbol", 8)
        )
        sample_offset = int(metadata.get(
            "example:sample_offset",
            samples_per_symbol // 2,
        ))
        alignment = (
            sample_offset - window.start_sample
        ) % samples_per_symbol
        symbols = samples[alignment::samples_per_symbol]
        eye_length = samples_per_symbol * 2
        eye_starts = np.arange(
            alignment,
            max(alignment, samples.size - eye_length),
            samples_per_symbol,
        )[:180]
        eye_segments = np.asarray([
            samples[start : start + eye_length]
            for start in eye_starts
        ])
        return CommsProducts(
            modulation,
            samples_per_symbol,
            symbols,
            np.arange(eye_length) / samples_per_symbol,
            eye_segments,
            float(metadata.get("example:constellation_limit", 1.2)),
            float(metadata.get("example:eye_limit", 1.05)),
            window.start_seconds,
            window.duration_seconds,
            window.buffer_nbytes,
        )


__all__ = ["CommsAnalysis"]
