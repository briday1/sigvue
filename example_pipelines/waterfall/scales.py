"""Data-driven display ranges for LTE power products."""

import numpy as np

from .models import WaterfallProducts


def automatic_dbfs_ranges(
    products: WaterfallProducts,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return robust waterfall and average-spectrum ranges, rounded to 5 dB."""
    waterfall = _finite(products.waterfall_dbfs)
    spectrum = _finite(products.spectrum_dbfs)
    if not waterfall.size or not spectrum.size:
        return (-90.0, -20.0), (-90.0, -20.0)

    # The upper waterfall tail preserves narrow carriers that occupy far less
    # than one percent of the raster. The time-averaged spectrum supplies a
    # second, outlier-resistant indication of real persistent signal energy.
    signal_top = max(
        float(np.percentile(waterfall, 99.9)),
        float(np.percentile(spectrum, 99.5)),
    )
    waterfall_range = _rounded_range(
        float(np.percentile(waterfall, 10.0)) - 3.0,
        signal_top + 3.0,
    )
    spectrum_range = _rounded_range(
        float(np.percentile(spectrum, 1.0)) - 3.0,
        float(np.percentile(spectrum, 99.9)) + 3.0,
    )
    return waterfall_range, spectrum_range


def _finite(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    return array[np.isfinite(array)]


def _rounded_range(lower: float, upper: float) -> tuple[float, float]:
    lower = max(-140.0, 5.0 * np.floor(lower / 5.0))
    upper = min(0.0, 5.0 * np.ceil(upper / 5.0))
    if upper - lower < 20.0:
        lower = max(-140.0, upper - 20.0)
    return float(lower), float(upper)
