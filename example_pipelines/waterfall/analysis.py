"""Numerical processing with no browser layout or styling decisions."""

import numpy as np

from sigvue.plugin import Analysis, ParameterContext

from .models import SignalWindow, WaterfallProducts, WaterfallSettings


def configure(data: SignalWindow, ui: ParameterContext) -> WaterfallSettings:
    return WaterfallSettings(
        fft_size=int(ui.select(
            "fft_size",
            label="FFT size (samples)",
            default=1024,
            options=(256, 512, 1024, 2048, 4096),
            group="Spectrogram processing",
        )),
        overlap_percent=int(ui.select(
            "overlap_percent",
            label="Overlap (%)",
            default=50,
            options=(0, 25, 50, 75),
            group="Spectrogram processing",
        )),
    )


def process(data: SignalWindow, settings: WaterfallSettings) -> WaterfallProducts:
    fft_size = min(settings.fft_size, data.samples.size)
    hop = max(1, round(fft_size * (1 - settings.overlap_percent / 100)))
    starts = np.arange(0, max(1, data.samples.size - fft_size + 1), hop, dtype=np.int64)
    blocks = np.asarray([data.samples[start : start + fft_size] for start in starts])
    if blocks.shape[1] < fft_size:
        blocks = np.pad(blocks, ((0, 0), (0, fft_size - blocks.shape[1])))
    taper = np.hanning(fft_size)
    spectra = np.fft.fftshift(np.fft.fft(blocks * taper, axis=1), axes=1)
    power = (np.abs(spectra) / max(float(np.sum(taper)), 1.0)) ** 2
    waterfall = 10 * np.log10(np.maximum(power, 1e-12))
    average = 10 * np.log10(np.maximum(np.mean(power, axis=0), 1e-12))
    frequency = (
        data.recording.center_frequency
        + np.fft.fftshift(np.fft.fftfreq(fft_size, 1 / data.recording.sample_rate))
    ) / 1e6
    centers = starts + fft_size / 2
    time_edges = cell_edges(centers, 0.0, float(data.samples.size))
    return WaterfallProducts(
        data.recording,
        data.start_sample,
        average,
        waterfall,
        frequency,
        (data.start_sample + time_edges) / data.recording.sample_rate * 1e3,
        data.samples.nbytes,
    )


def cell_edges(centers: np.ndarray, lower: float, upper: float) -> np.ndarray:
    if centers.size == 1:
        return np.asarray([lower, upper])
    return np.concatenate(([lower], (centers[:-1] + centers[1:]) / 2, [upper]))


class WaterfallAnalysis(Analysis[SignalWindow, WaterfallSettings, WaterfallProducts]):
    """Framework analysis object for the synthetic LTE waterfall."""

    def configure(self, data: SignalWindow, ui: ParameterContext) -> WaterfallSettings:
        return configure(data, ui)

    def process(
        self,
        data: SignalWindow,
        settings: WaterfallSettings | None,
    ) -> WaterfallProducts:
        if settings is None:
            raise RuntimeError("Waterfall analysis requires configured settings")
        return process(data, settings)
