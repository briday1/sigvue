#!/usr/bin/env python3
"""Generate small LTE-like uplink and downlink SigMF recordings."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from example_pipelines.plugins.sigmf import write_sigmf_recording


SAMPLE_RATE = 1_920_000.0


def ofdm_signal(*, seed: int, duration: float, uplink: bool) -> np.ndarray:
    rng = np.random.default_rng(seed)
    fft_size = 128
    cyclic_prefix = 9
    frame_size = fft_size + cyclic_prefix
    symbol_count = int(np.ceil(duration * SAMPLE_RATE / frame_size))
    grid = np.zeros((symbol_count, fft_size), dtype=np.complex64)
    active = np.r_[np.arange(10, 46), np.arange(82, 118)]
    qpsk = (2 * rng.integers(0, 2, (symbol_count, active.size)) - 1) + 1j * (
        2 * rng.integers(0, 2, (symbol_count, active.size)) - 1
    )
    if uplink:
        qpsk = np.fft.fft(qpsk, axis=1) / np.sqrt(active.size)
    grid[:, active] = qpsk / np.sqrt(2)
    symbols = np.fft.ifft(np.fft.ifftshift(grid, axes=1), axis=1) * np.sqrt(fft_size)
    with_prefix = np.concatenate((symbols[:, -cyclic_prefix:], symbols), axis=1).reshape(-1)
    samples = with_prefix[: round(duration * SAMPLE_RATE)]
    envelope = np.ones(samples.size)
    if uplink:
        period = round(0.004 * SAMPLE_RATE)
        on = round(0.0028 * SAMPLE_RATE)
        envelope = (np.arange(samples.size) % period < on).astype(float)
    offset_hz = -110_000.0 if uplink else 75_000.0
    samples *= np.exp(1j * 2 * np.pi * offset_hz * np.arange(samples.size) / SAMPLE_RATE)
    noise = (rng.normal(size=samples.size) + 1j * rng.normal(size=samples.size)) * 0.025
    samples = (0.34 * envelope * samples + noise).astype(np.complex64)
    return samples / max(1.0, float(np.max(np.abs(samples))) / 0.86)


def write_recording(root: Path, direction: str, center_frequency: float, seed: int) -> tuple[Path, Path]:
    stem = f"synthetic-lte-{direction}"
    samples = ofdm_signal(seed=seed, duration=0.12, uplink=direction == "uplink")
    return write_sigmf_recording(
        root,
        stem,
        samples,
        SAMPLE_RATE,
        datatype="ci16_le",
        description=f"Synthetic LTE-like {direction}",
        global_metadata={
            "core:author": "Sigvue example generator",
            "example:direction": direction,
        },
        captures=({
            "core:sample_start": 0,
            "core:frequency": center_frequency,
            "core:datetime": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        },),
    )


def generate(root: Path) -> tuple[tuple[Path, Path], ...]:
    root.mkdir(parents=True, exist_ok=True)
    _remove_legacy_direction_folders(root)
    return (
        write_recording(root, "downlink", 806_000_000.0, 20260719),
        write_recording(root, "uplink", 847_000_000.0, 20260720),
    )


def _remove_legacy_direction_folders(root: Path) -> None:
    """Remove only files made by the former direction-subfolder layout."""
    for direction in ("downlink", "uplink"):
        directory = root / direction
        stem = f"synthetic-lte-{direction}"
        for suffix in (".sigmf-meta", ".sigmf-data"):
            path = directory / f"{stem}{suffix}"
            if path.is_file():
                path.unlink()
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/lte",
        help="Output data directory (default: example_pipelines/data/lte)",
    )
    args = parser.parse_args()
    for metadata_path, data_path in generate(args.output.resolve()):
        print(f"Wrote {metadata_path}")
        print(f"Wrote {data_path}")


if __name__ == "__main__":
    main()
